import asyncio
import json
import logging
import os
from urllib.parse import urlencode

import httpx

import config

logger = logging.getLogger(__name__)

API_PREFIX = "/en-US/splunkd/__raw"

SPLUNK_COOKIE_NAMES = ("splunkd_8008", "session_id_8008", "splunkweb_csrf_token_8008", "token_key")


class SplunkAPIError(Exception):
    pass


class SessionExpiredError(SplunkAPIError):
    pass


class SplunkAPIClient:
    def __init__(self):
        self.base_url = config.SPLUNK_URL.rstrip("/")
        self.session_path = config.SPLUNK_API_SESSION_PATH
        self._client: httpx.AsyncClient | None = None
        self._cookies: dict = {}
        self._csrf_token: str | None = None
        self._user_id: str | None = None

    async def start(self):
        session_data = self._load_session()
        if session_data is None:
            raise SplunkAPIError(
                f"API session file {self.session_path} not found. "
                "Run 'python save_session.py' first."
            )

        self._cookies = session_data["cookies"]
        self._csrf_token = session_data.get("csrf_token") or self._cookies.get(
            "splunkweb_csrf_token_8008"
        )

        cookie_jar = httpx.Cookies()
        for name, value in self._cookies.items():
            host = self.base_url.split("://", 1)[1].split(":")[0].split("/")[0]
            cookie_jar.set(name, value, domain=host)

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            cookies=cookie_jar,
            headers={
                "X-Splunk-Form-Key": self._csrf_token or "",
                "X-Requested-With": "XMLHttpRequest",
            },
            verify=not config.SPLUNK_SKIP_SSL_VERIFY,
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=False,
        )

        logger.info(
            "Splunk API client started (base_url=%s, csrf=%s...)",
            self.base_url,
            (self._csrf_token or "")[:20],
        )

    def _load_session(self) -> dict | None:
        if not os.path.exists(self.session_path):
            return None
        try:
            with open(self.session_path, "r") as f:
                data = json.load(f)
            missing = [c for c in SPLUNK_COOKIE_NAMES if c not in data.get("cookies", {})]
            if missing:
                logger.warning("API session missing cookies: %s", missing)
            return data
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Failed to parse API session file: %s", e)
            return None

    def _api_url(self, path: str) -> str:
        return f"{API_PREFIX}{path}"

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if self._client is None:
            raise SplunkAPIError("API client not started")

        url = self._api_url(path)
        try:
            response = await self._client.request(method, url, **kwargs)
        except httpx.ConnectError as e:
            raise SplunkAPIError(f"Cannot connect to Splunk: {e}") from e
        except httpx.TimeoutException as e:
            raise SplunkAPIError(f"Request timed out: {e}") from e

        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location", "")
            if config.SPLUNK_SSO_DOMAIN.lower() in location.lower():
                raise SessionExpiredError(f"Redirected to SSO: {location}")
            logger.warning("Unexpected redirect to: %s", location)

        if response.status_code == 401:
            raise SessionExpiredError("HTTP 401 Unauthorized — session expired")

        if response.status_code == 403:
            raise SessionExpiredError("HTTP 403 Forbidden — session expired or insufficient permissions")

        return response

    async def search(
        self, spl_query: str, time_range: str = "24h"
    ) -> tuple[str, str | None]:
        logger.info("API search: query=%s time_range=%s", spl_query[:80], time_range)

        try:
            sid = await self._create_job(spl_query, time_range)
        except Exception as e:
            logger.error("Failed to create search job: %s", e)
            if isinstance(e, SessionExpiredError):
                return ("session_expired", None)
            return ("error", None)

        try:
            job_data = await self._poll_job(sid)
        except SessionExpiredError:
            return ("session_expired", None)
        except Exception as e:
            logger.error("Failed to poll search job %s: %s", sid, e)
            return ("error", None)

        event_count = job_data.get("eventCount", 0)
        if event_count == 0:
            logger.info("No events found for sid=%s", sid)
            await self._cleanup_job(sid)
            return ("no_logs", None)

        try:
            log_lines = await self._get_events(sid)
        except SessionExpiredError:
            return ("session_expired", None)
        except Exception as e:
            logger.error("Failed to get events for sid=%s: %s", sid, e)
            return ("error", None)
        finally:
            await self._cleanup_job(sid)

        if not log_lines.strip():
            return ("no_logs", None)

        logger.info("API search complete: sid=%s, %d chars", sid, len(log_lines))
        return ("success", log_lines)

    async def _create_job(self, spl_query: str, time_range: str) -> str:
        params = {
            "search": f"search {spl_query}",
            "earliest_time": f"-{time_range}",
            "latest_time": "now",
            "output_mode": "json",
            "rf": "*",
            "status_buckets": 300,
            "auto_cancel": 120,
        }

        response = await self._request(
            "POST",
            "/services/search/v2/jobs",
            content=urlencode(params),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if response.status_code not in (200, 201):
            raise SplunkAPIError(
                f"Create job failed: HTTP {response.status_code} — {response.text[:200]}"
            )

        try:
            data = response.json()
        except json.JSONDecodeError:
            raise SplunkAPIError(f"Invalid JSON response: {response.text[:200]}")

        sid = data.get("sid")
        if not sid:
            raise SplunkAPIError(f"No sid in response: {response.text[:200]}")

        logger.info("Created search job: sid=%s", sid)
        return sid

    async def _poll_job(self, sid: str) -> dict:
        timeout = config.SPLUNK_RESULT_WAIT_TIMEOUT
        interval = config.SPLUNK_POLL_INTERVAL
        elapsed = 0

        while elapsed < timeout:
            response = await self._request(
                "GET",
                f"/services/search/v2/jobs/{sid}",
                params={"output_mode": "json"},
            )

            if response.status_code != 200:
                raise SplunkAPIError(
                    f"Poll job failed: HTTP {response.status_code} — {response.text[:200]}"
                )

            data = response.json()

            entries = data.get("entry", [])
            if not entries:
                raise SplunkAPIError(f"No entry in job response: {data}")

            content = entries[0].get("content", {})
            is_done = content.get("isDone", False)

            if is_done:
                dispatch_state = content.get("dispatchState", "UNKNOWN")
                if dispatch_state in ("FAILED", "INTERNAL_CANCEL", "BAD_INPUT_CANCEL", "QUIT"):
                    raise SplunkAPIError(f"Job ended with state: {dispatch_state}")

                return {
                    "eventCount": content.get("eventCount", 0),
                    "resultCount": content.get("resultCount", 0),
                }

            await asyncio.sleep(interval)
            elapsed += interval

        raise SplunkAPIError(f"Job poll timed out after {timeout}s (sid={sid})")

    async def _get_events(self, sid: str) -> str:
        response = await self._request(
            "GET",
            f"/services/search/v2/jobs/{sid}/events",
            params={
                "output_mode": "json",
                "offset": 0,
                "count": config.MAX_LOG_LINES,
                "field_list": "_raw,_time,source,sourcetype,host",
                "max_lines": 0,
                "segmentation": "raw",
            },
        )

        if response.status_code != 200:
            raise SplunkAPIError(
                f"Get events failed: HTTP {response.status_code} — {response.text[:200]}"
            )

        data = response.json()
        results = data.get("results", [])

        if not results:
            return ""

        lines = []
        for event in results:
            raw = event.get("_raw", "")
            if raw:
                timestamp = event.get("_time", "")
                if timestamp and timestamp != "0":
                    lines.append(f"[{timestamp}] {raw}")
                else:
                    lines.append(raw)

        return "\n".join(lines)

    async def _cleanup_job(self, sid: str):
        try:
            await self._request(
                "POST",
                f"/services/search/v2/jobs/{sid}/control",
                content=urlencode({"action": "cancel", "output_mode": "json"}),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except Exception:
            logger.debug("Job cleanup failed for sid=%s (ignored)", sid)

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("Splunk API client closed")


splunk_api = SplunkAPIClient()