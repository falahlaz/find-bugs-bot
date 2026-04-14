import shlex
import re
import logging

import config

logger = logging.getLogger(__name__)


class CurlParseError(Exception):
    pass


def _normalize_multiline(curl_str: str) -> str:
    return curl_str.replace("\\\n", " ").replace("\\\r\n", " ")


def parse_curl(raw: str) -> dict:
    normalized = _normalize_multiline(raw.strip())

    if not normalized.lower().startswith("curl"):
        raise CurlParseError(
            "That doesn't look like a valid curl command. Please paste the full curl command."
        )

    try:
        tokens = shlex.split(normalized)
    except ValueError as e:
        raise CurlParseError(f"Could not parse curl command: {e}")

    method = None
    url = None
    headers = {}
    body = None

    i = 1
    while i < len(tokens):
        token = tokens[i]

        if token in ("-X", "--request") and i + 1 < len(tokens):
            method = tokens[i + 1].upper()
            i += 2
        elif token in ("-H", "--header") and i + 1 < len(tokens):
            header_val = tokens[i + 1]
            if ":" in header_val:
                key, _, value = header_val.partition(":")
                headers[key.strip().lower()] = value.strip()
            i += 2
        elif token in ("-d", "--data", "--data-raw", "--data-binary", "--data-urlencode") and i + 1 < len(tokens):
            body = tokens[i + 1]
            if not method:
                method = "POST"
            i += 2
        elif token == "--data-ascii" and i + 1 < len(tokens):
            body = tokens[i + 1]
            if not method:
                method = "POST"
            i += 2
        elif token in ("--url") and i + 1 < len(tokens):
            url = tokens[i + 1]
            i += 2
        elif not token.startswith("-") and url is None:
            url = token
            i += 1
        else:
            i += 1

    if url is None:
        raise CurlParseError("Could not find a URL in the curl command.")

    if not method:
        method = "GET" if body is None else "POST"

    header_name_lower = config.TRANSACTION_ID_HEADER.lower()
    txn_id = headers.get(header_name_lower)

    if txn_id is None:
        for key, value in headers.items():
            if key == header_name_lower:
                txn_id = value
                break

    if txn_id is None:
        raise CurlParseError(
            f"Could not find `{config.TRANSACTION_ID_HEADER}` header in the curl. "
            f"Please make sure it's included."
        )

    pretty_headers = {}
    for key, value in headers.items():
        pretty_headers[key if key == key.lower() else key] = value

    return {
        "transaction_id": txn_id,
        "method": method,
        "url": url,
        "headers": headers,
        "body": body,
    }