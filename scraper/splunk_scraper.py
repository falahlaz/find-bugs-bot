import logging

import config
from scraper.correlation import resolve_backend_id
from scraper.splunk_api import splunk_api, SessionExpiredError, SplunkAPIError

logger = logging.getLogger(__name__)


def _build_query(template: str, transaction_id: str) -> str:
    return template.format(transaction_id=transaction_id) + " NOT kong"


async def _run_search(
    template: str, transaction_id: str, time_range: str
) -> tuple[str, str | None]:
    spl_query = _build_query(template, transaction_id)

    try:
        return await splunk_api.search(spl_query, time_range)
    except SessionExpiredError as e:
        logger.warning("Splunk session expired: %s", e)
        return ("session_expired", None)
    except SplunkAPIError as e:
        logger.error("Splunk API error for transaction_id=%s: %s", transaction_id, e)
        return ("error", None)
    except Exception as e:
        logger.exception(
            "Unexpected error querying Splunk API for transaction_id=%s: [%s] %s",
            transaction_id,
            type(e).__name__,
            e,
        )
        return ("error", None)


async def scrape_splunk(
    transaction_id: str, environment: str = "prod", time_range: str = "24h"
) -> tuple[str, str | None, str | None]:
    """Search Splunk, following the client-id -> backend-id link when present.

    Returns (status, log_lines, resolved_transaction_id). The third element is
    set only when a second search was run and its logs are the ones returned.
    """
    template = config.SPLUNK_SPL_TEMPLATES.get(environment)
    if template is None:
        logger.error(
            "Unknown environment: %s. Available: %s",
            environment,
            list(config.SPLUNK_SPL_TEMPLATES.keys()),
        )
        return ("error", None, None)

    logger.info(
        "Searching Splunk for transaction_id=%s environment=%s time_range=%s",
        transaction_id,
        environment,
        time_range,
    )

    status, log_data = await _run_search(template, transaction_id, time_range)

    if status != "success" or not config.SPLUNK_CORRELATION_ENABLED:
        return (status, log_data, None)

    backend_id = resolve_backend_id(log_data, transaction_id)
    if not backend_id:
        return (status, log_data, None)

    logger.info(
        "Resolved backend transaction_id=%s for client transaction_id=%s, re-searching",
        backend_id,
        transaction_id,
    )

    backend_status, backend_logs = await _run_search(template, backend_id, time_range)

    if backend_status == "success":
        return ("success", backend_logs, backend_id)

    if backend_status == "session_expired":
        # Let the caller re-auth and re-queue the whole job.
        return ("session_expired", None, None)

    # Second search came back empty or errored — keep the first-hop logs rather
    # than reporting a backend id whose logs we never used.
    logger.warning(
        "Backend search for %s returned %s, falling back to logs for %s",
        backend_id,
        backend_status,
        transaction_id,
    )
    return ("success", log_data, None)
