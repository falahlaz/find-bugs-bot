import logging

import config
from scraper.splunk_api import splunk_api, SessionExpiredError, SplunkAPIError

logger = logging.getLogger(__name__)


async def scrape_splunk(
    transaction_id: str, environment: str = "prod", time_range: str = "24h"
) -> tuple[str, str | None]:
    template = config.SPLUNK_SPL_TEMPLATES.get(environment)
    if template is None:
        logger.error(
            "Unknown environment: %s. Available: %s",
            environment,
            list(config.SPLUNK_SPL_TEMPLATES.keys()),
        )
        return ("error", None)

    spl_query = template.format(transaction_id=transaction_id)

    logger.info(
        "Searching Splunk for transaction_id=%s environment=%s time_range=%s",
        transaction_id,
        environment,
        time_range,
    )

    try:
        result_status, log_data = await splunk_api.search(spl_query, time_range)
        return (result_status, log_data)
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