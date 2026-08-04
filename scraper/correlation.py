"""Resolve a backend-generated transaction ID from a first-hop Splunk result.

QA reads the transaction ID off the request header, but several backend services
mint their own `_id` and log everything under that instead. Such a service emits
exactly one event carrying both — the "API Request" event:

    {"tags": ["API Request"], "data": {
        "_id": "A100000000000000000000001",              <- backend id
        "mobileapptransactionid": "A100000000000000000000002"}}   <- header id

`resolve_backend_id` finds that event and returns the backend id, so the caller
can re-run the search against the ID the real trace is keyed on.
"""

import json
import logging
import re

from parser.validation import is_valid_transaction_id

logger = logging.getLogger(__name__)

BACKEND_ID_KEY = "_id"

# Fields that may echo the ID the client sent. `_id` is deliberately excluded:
# an event whose `_id` already equals the searched ID proves nothing, and
# chasing it would walk the correlation backwards.
LINKED_ID_KEYS = (
    "mobileapptransactionid",
    "mobiletransactionid",
    "transactionid",
    "transaction_id",
)

_TIMESTAMP_PREFIX_RE = re.compile(r"^\[[^\]]*\]\s*")

_ID_FIELD_RE = re.compile(
    r'"(' + "|".join((BACKEND_ID_KEY, *LINKED_ID_KEYS)) + r')"\s*:\s*"([^"]*)"'
)


def _strip_timestamp(line: str) -> str:
    """Drop the `[<_time>] ` prefix that `SplunkAPIClient._get_events` prepends."""
    return _TIMESTAMP_PREFIX_RE.sub("", line, count=1)


def _ids_in_line(line: str) -> dict[str, str]:
    """Correlation-ID fields found in one log line, keyed by lowercased field name.

    Prefers parsing the JSON payload; falls back to a regex scan for lines that
    aren't JSON (Kong text) or that Splunk truncated mid-object.
    """
    payload = _strip_timestamp(line).strip()

    try:
        event = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        event = None

    if isinstance(event, dict):
        data = event.get("data")
        if isinstance(data, dict):
            found = {}
            for key, value in data.items():
                lowered = key.lower()
                if lowered in (BACKEND_ID_KEY, *LINKED_ID_KEYS) and isinstance(value, str):
                    found[lowered] = value
            return found
        return {}

    return {
        key.lower(): value
        for key, value in _ID_FIELD_RE.findall(payload)
    }


def resolve_backend_id(log_lines: str, searched_id: str) -> str | None:
    """Return the backend `_id` linked to `searched_id`, or None.

    Only a *directed* link counts: within a single event, some non-`_id`
    correlation field must equal `searched_id` while `_id` differs. Searching a
    backend `_id` therefore never resolves to anything, and no second search is
    fired.
    """
    if not log_lines or not searched_id:
        return None

    target = searched_id.casefold()
    candidates: dict[str, int] = {}

    for line in log_lines.splitlines():
        if not line.strip():
            continue

        ids = _ids_in_line(line)
        backend_id = ids.get(BACKEND_ID_KEY)
        if not backend_id or backend_id.casefold() == target:
            continue

        linked = any(
            ids.get(key, "").casefold() == target for key in LINKED_ID_KEYS
        )
        if not linked:
            continue

        if not is_valid_transaction_id(backend_id):
            logger.warning(
                "Discarding malformed backend id linked to %s: %r", searched_id, backend_id[:64]
            )
            continue

        candidates[backend_id] = candidates.get(backend_id, 0) + 1

    if not candidates:
        return None

    # Most frequently linked wins; dict preserves insertion order, so ties go to
    # the first one seen.
    best = max(candidates, key=lambda cid: candidates[cid])

    if len(candidates) > 1:
        logger.warning(
            "Multiple backend ids linked to %s: %s — using %s",
            searched_id, sorted(candidates), best,
        )

    return best
