import re

TRANSACTION_ID_RE = re.compile(r"[A-Za-z0-9\-_.]{1,128}")


def is_valid_transaction_id(value: str | None) -> bool:
    """Allowlist check for anything interpolated into an SPL query.

    SPL templates in `SPLUNK_SPL_TEMPLATES` are formatted with no escaping, so
    every transaction ID must pass this before reaching the query builder —
    including IDs discovered inside log events, which never cross a user-input
    boundary.
    """
    if not value:
        return False
    return TRANSACTION_ID_RE.fullmatch(value) is not None
