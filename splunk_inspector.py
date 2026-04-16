"""
Splunk API Network Inspector
----------------------------
Run: python splunk_inspector.py

Opens a headed browser with your SSO session, navigates to Splunk,
and intercepts all network requests — capturing every XHR/fetch call
the Splunk UI makes, especially during search operations.

Outputs a structured discovery report with:
  - Authentication mechanism (cookies, tokens, CSRF)
  - API endpoints discovered (URL, method, headers, body, response)
  - Suggested API client configuration

Press 'q' in terminal OR close the browser window to stop and print the report.
"""

import asyncio
import json
import logging
import sys
from datetime import datetime
from urllib.parse import urlparse, parse_qs, unquote

from playwright.async_api import async_playwright, Page, Request, Response

import config

logging.basicConfig(
    level=logging.INFO,
    format="\033[94m[%(asctime)s]\033[0m %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("splunk_inspector")

MAX_RESPONSE_BODY = 10240
API_URL_PATTERNS = (
    "/services/",
    "/splunkd/",
    "/api/",
    "/en-US/splunkd/",
    "/en-US/api/",
    "/__api/",
)
SKIP_EXTENSIONS = (
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".ico", ".woff", ".woff2", ".ttf", ".eot", ".map",
    ".mp4", ".webm", ".avi", ".mov",
)


class NetworkCapture:
    def __init__(self):
        self.api_requests: list[dict] = []
        self.auth_info: dict = {}
        self._seen_urls: set[str] = set()

    def _is_api_request(self, url: str) -> bool:
        path = urlparse(url).path.lower()
        if path.endswith(SKIP_EXTENSIONS):
            return False
        return any(pattern in path for pattern in API_URL_PATTERNS)

    def _extract_auth(self, headers: dict) -> None:
        if not self.auth_info.get("type"):
            if headers.get("authorization"):
                self.auth_info["type"] = "header"
                self.auth_info["details"] = {"Authorization": headers["authorization"]}
            elif headers.get("cookie"):
                self.auth_info["type"] = "cookie"
                cookies = {}
                for part in headers["cookie"].split(";"):
                    part = part.strip()
                    if "=" in part:
                        k, v = part.split("=", 1)
                        cookies[k.strip()] = v.strip()
                self.auth_info["details"] = cookies

        for key in headers:
            key_lower = key.lower()
            if "csrf" in key_lower or "token" in key_lower or "auth" in key_lower:
                self.auth_info.setdefault("custom_headers", {})[key] = headers[key]

    async def record_request(self, request: Request) -> None:
        url = request.url
        if not self._is_api_request(url):
            return

        method = request.method
        headers = await request.all_headers()
        headers_dict = {k.lower(): v for k, v in headers.items()}
        self._extract_auth(headers_dict)

        post_data = None
        if method.upper() == "POST":
            try:
                post_data = request.post_data
            except Exception:
                pass

        entry = {
            "timestamp": datetime.now().isoformat(),
            "method": method.upper(),
            "url": url,
            "path": urlparse(url).path,
            "query": urlparse(url).query,
            "headers": headers_dict,
            "post_data": post_data,
            "response_status": None,
            "response_body": None,
        }
        self.api_requests.append(entry)
        self._seen_urls.add(f"{method.upper()} {urlparse(url).path}")

        logger.info(
            "\033[93m→ %s %s\033[0m %s",
            method.upper(),
            urlparse(url).path,
            f"body={post_data[:80]}..." if post_data and len(post_data) > 80 else (post_data or ""),
        )

    async def record_response(self, response: Response) -> None:
        url = response.url
        if not self._is_api_request(url):
            return

        request = response.request
        method = request.method.upper()
        path = urlparse(url).path
        key = f"{method} {path}"

        for entry in reversed(self.api_requests):
            if entry["url"] == url and entry["method"] == method and entry["response_status"] is None:
                entry["response_status"] = response.status
                try:
                    body = await response.text()
                    if len(body) > MAX_RESPONSE_BODY:
                        body = body[:MAX_RESPONSE_BODY] + f"\n... [truncated, total {len(body)} bytes]"
                    entry["response_body"] = body
                except Exception as e:
                    entry["response_body"] = f"[could not read body: {e}]"

                logger.info(
                    "\033[92m← %s %s\033[0m → %d",
                    method,
                    path,
                    response.status,
                )
                break

    def generate_report(self) -> str:
        lines = []
        lines.append("\n" + "=" * 70)
        lines.append("SPLUNK API DISCOVERY REPORT")
        lines.append("=" * 70)

        lines.append("\n--- AUTHENTICATION ---")
        if self.auth_info:
            auth_type = self.auth_info.get("type", "unknown")
            lines.append(f"  Auth Type: {auth_type}")
            details = self.auth_info.get("details", {})
            if auth_type == "cookie":
                for name, value in details.items():
                    short_val = value[:40] + "..." if len(value) > 40 else value
                    lines.append(f"  Cookie: {name}={short_val}")
            elif auth_type == "header":
                for name, value in details.items():
                    short_val = value[:40] + "..." if len(value) > 40 else value
                    lines.append(f"  Header: {name}={short_val}")
            custom = self.auth_info.get("custom_headers", {})
            if custom:
                lines.append("  Custom Auth Headers:")
                for name, value in custom.items():
                    short_val = value[:60] + "..." if len(value) > 60 else value
                    lines.append(f"    {name}: {short_val}")
        else:
            lines.append("  No auth headers or cookies intercepted.")

        lines.append("\n--- API ENDPOINTS DISCOVERED ---")
        endpoint_summary: dict[str, list[dict]] = {}
        for entry in self.api_requests:
            ep_key = f"{entry['method']} {entry['path']}"
            endpoint_summary.setdefault(ep_key, []).append(entry)

        for i, (ep_key, entries) in enumerate(endpoint_summary.items(), 1):
            first = entries[0]
            lines.append(f"\n  {i}. {ep_key}")
            if first.get("post_data"):
                decoded = unquote(first["post_data"])
                lines.append(f"     Request Body (first call):")
                parts = decoded.split("&")
                for part in parts[:10]:
                    if "=" in part:
                        k, v = part.split("=", 1)
                        lines.append(f"       {k} = {v[:80]}")
                if len(parts) > 10:
                    lines.append(f"       ... + {len(parts) - 10} more params")

            resp_status = first.get("response_status")
            if resp_status:
                lines.append(f"     Response Status: {resp_status}")

            resp_body = first.get("response_body")
            if resp_body:
                try:
                    parsed = json.loads(resp_body)
                    if isinstance(parsed, dict):
                        top_keys = [k for k in list(parsed.keys())[:8]]
                        lines.append(f"     Response Body (top-level keys): {', '.join(top_keys)}")
                        if "entry" in parsed and isinstance(parsed["entry"], list):
                            lines.append(f"       entry count: {len(parsed['entry'])}")
                        if "results" in parsed and isinstance(parsed["results"], list):
                            lines.append(f"       results count: {len(parsed['results'])}")
                        if "sid" in parsed:
                            lines.append(f"       sid: {parsed['sid']}")
                    elif isinstance(parsed, str):
                        lines.append(f"     Response Body: {parsed[:120]}")
                except (json.JSONDecodeError, TypeError):
                    body_preview = resp_body[:200].replace("\n", " ")
                    lines.append(f"     Response Body (preview): {body_preview}")

            query = first.get("query", "")
            if query:
                qs = parse_qs(query)
                if qs:
                    lines.append(f"     Query Params:")
                    for k, v in qs.items():
                        lines.append(f"       {k} = {v[0][:80]}")

            if len(entries) > 1:
                lines.append(f"     (called {len(entries)} times)")

        lines.append("\n--- SUGGESTED API CLIENT CONFIG ---")
        url_parts = urlparse(config.SPLUNK_URL)
        base_host = f"{url_parts.scheme}://{url_parts.hostname}"
        if url_parts.port:
            base_host += f":{url_parts.port}"

        paths_seen = [ep_key for ep_key in endpoint_summary]
        lines.append(f"  BASE_URL: {base_host}")
        lines.append(f"  AUTH_METHOD: {self.auth_info.get('type', 'cookie')}")

        csrf_headers = self.auth_info.get("custom_headers", {})
        lines.append(f"  CSRF_REQUIRED: {'yes' if csrf_headers else 'unknown'}")

        lines.append("\n  KEY_ENDPOINTS:")
        search_endpoints = [p for p in paths_seen if "search" in p.lower() or "jobs" in p.lower()]
        if search_endpoints:
            for ep in search_endpoints:
                lines.append(f"    - {ep}")
        else:
            lines.append("    - POST   /services/search/jobs              (create search)")
            lines.append("    - GET    /services/search/jobs/{{sid}}          (poll status)")
            lines.append("    - GET    /services/search/v2/jobs/{{sid}}/results (fetch results)")
            lines.append("    - DELETE /services/search/jobs/{{sid}}          (cleanup)")

        lines.append("\n" + "=" * 70)

        lines.append("\n--- RAW REQUEST LOG ---")
        for i, entry in enumerate(self.api_requests, 1):
            lines.append(f"\n  [{i}] {entry['method']} {entry['path']}")
            if entry.get("post_data"):
                lines.append(f"      Body: {unquote(entry['post_data'])[:200]}")
            if entry.get("response_status"):
                lines.append(f"      Status: {entry['response_status']}")
            if entry.get("query"):
                lines.append(f"      Query: {entry['query']}")

        lines.append("\n" + "=" * 70)
        return "\n".join(lines)


async def main():
    logger.info("Starting Splunk API Network Inspector...")
    logger.info(f"Target URL: {config.SPLUNK_URL}")
    logger.info("Perform a search in the browser. All API calls will be captured.\n")

    session_file = config.SPLUNK_SESSION_PATH
    if session_file and getattr(sys, "platform", "") != "":
        import os
        if os.path.exists(session_file):
            logger.info(f"Loading session from: {session_file}")
        else:
            logger.warning(f"Session file not found: {session_file} (may need manual login)")

    capture = NetworkCapture()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            storage_state=session_file if (session_file and __import__("os").path.exists(session_file)) else None,
            viewport={"width": 1280, "height": 720},
            ignore_https_errors=True,
        )
        page = await context.new_page()

        page.on("request", lambda req: asyncio.ensure_future(capture.record_request(req)))
        page.on("response", lambda resp: asyncio.ensure_future(capture.record_response(resp)))

        logger.info(f"Navigating to: {config.SPLUNK_URL}")
        await page.goto(config.SPLUNK_URL, wait_until="domcontentloaded", timeout=60000)

        logger.info("\n" + "=" * 70)
        logger.info("INSPECTOR ACTIVE — Perform searches in the browser!")
        logger.info("All API requests/responses are being captured.")
        logger.info("Press 'q' in this terminal or close the browser to stop.")
        logger.info("=" * 70 + "\n")

        async def check_quit():
            loop = asyncio.get_event_loop()
            fut = loop.create_future()

            def reader():
                try:
                    line = sys.stdin.readline()
                    if line:
                        fut.set_result(line.strip().lower() == "q")
                    else:
                        fut.set_result(False)
                except Exception:
                    fut.set_result(False)

            import threading
            t = threading.Thread(target=reader, daemon=True)
            t.start()

            try:
                result = await asyncio.wait_for(fut, timeout=0.5)
            except asyncio.TimeoutError:
                return False
            return result

        try:
            while True:
                if page.is_closed():
                    break
                quit_requested = await check_quit()
                if quit_requested:
                    break
                await asyncio.sleep(0.5)
        except KeyboardInterrupt:
            pass

        if not page.is_closed():
            try:
                await page.close()
            except Exception:
                pass

        try:
            await browser.close()
        except Exception:
            pass

    report = capture.generate_report()
    print(report)

    import os
    report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evidences")
    os.makedirs(report_dir, exist_ok=True)
    report_file = os.path.join(report_dir, f"splunk_api_inspect_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
    with open(report_file, "w") as f:
        f.write(report)
    logger.info(f"Report saved to: {report_file}")

    raw_file = os.path.join(report_dir, f"splunk_api_inspect_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(raw_file, "w") as f:
        json.dump(capture.api_requests, f, indent=2, default=str)
    logger.info(f"Raw data saved to: {raw_file}")


if __name__ == "__main__":
    asyncio.run(main())