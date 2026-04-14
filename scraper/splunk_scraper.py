import logging

from playwright.async_api import Page, Error as PlaywrightError

import config
from scraper.browser import browser_manager

logger = logging.getLogger(__name__)

SPLUNK_SEARCH_INPUT = 'textarea[data-test="search-input"], textarea.spl-search-input, input[data-test="search-input"]'
SPLUNK_SEARCH_BUTTON = 'button[data-test="search-button"], button.search-btn'
SPLUNK_RESULTS_CONTAINER = 'div[data-test="results-container"], div.results-table, div.event-list'
SPLUNK_SSO_INDICATOR = config.SPLUNK_SSO_DOMAIN


async def scrape_splunk(transaction_id: str, environment: str = "prod") -> tuple[str, str | None]:
    template = config.SPLUNK_SPL_TEMPLATES.get(environment)
    if template is None:
        logger.error("Unknown environment: %s. Available: %s", environment, list(config.SPLUNK_SPL_TEMPLATES.keys()))
        return ("error", None)
    spl_query = template.format(transaction_id=transaction_id)
    logger.info("Scraping Splunk for transaction_id=%s environment=%s", transaction_id, environment)

    page = None
    try:
        page = await browser_manager.get_page()

        await page.goto(config.SPLUNK_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_load_state("networkidle", timeout=15000)

        current_url = page.url
        if SPLUNK_SSO_INDICATOR.lower() in current_url.lower():
            logger.warning("Splunk session expired (redirected to SSO at %s)", current_url)
            return ("session_expired", None)

        try:
            search_input = page.locator(SPLUNK_SEARCH_INPUT).first
            await search_input.fill("", timeout=5000)
            await search_input.fill(spl_query, timeout=10000)
            logger.info("Typed SPL query: %s", spl_query[:80])
        except Exception:
            search_input = page.locator("textarea").first
            await search_input.click()
            await page.keyboard.press("Control+a")
            await page.keyboard.press("Backspace")
            await search_input.type(spl_query, delay=30)
            logger.info("Typed SPL query via fallback method")

        try:
            search_btn = page.locator(SPLUNK_SEARCH_BUTTON).first
            await search_btn.click(timeout=5000)
        except Exception:
            await page.keyboard.press("Enter")
            logger.info("Submitted search via Enter key")

        try:
            await page.wait_for_selector(
                SPLUNK_RESULTS_CONTAINER,
                timeout=config.SPLUNK_RESULT_WAIT_TIMEOUT * 1000,
            )
        except Exception:
            body_text = await page.locator("body").inner_text()
            if "no results" in body_text.lower() or "0 events" in body_text.lower() or "0 of" in body_text.lower():
                logger.info("No results found for transaction_id=%s", transaction_id)
                return ("no_logs", None)
            logger.warning("Timed out waiting for results, checking page content...")
            await page.wait_for_timeout(5000)

        log_lines = await _extract_log_lines(page)

        if not log_lines.strip():
            logger.info("Empty results for transaction_id=%s", transaction_id)
            return ("no_logs", None)

        logger.info("Extracted %d characters of log data for transaction_id=%s", len(log_lines), transaction_id)
        return ("success", log_lines)

    except PlaywrightError as e:
        logger.error("Playwright error for transaction_id=%s: %s", transaction_id, e)
        if browser_manager.can_restart:
            restarted = await browser_manager.restart()
            if restarted:
                return ("browser_restarted", None)
        return ("browser_error", None)
    except Exception as e:
        logger.exception("Unexpected error scraping Splunk for transaction_id=%s", transaction_id)
        return ("error", None)
    finally:
        if page and not page.is_closed():
            try:
                await page.close()
            except Exception:
                pass


async def _extract_log_lines(page: Page) -> str:
    max_lines = config.MAX_LOG_LINES

    try:
        rows = page.locator("tr[data-test='event-row'], tr.event-row, tr[data-row-id]")
        count = await rows.count()

        if count > 0:
            lines = []
            for i in range(min(count, max_lines)):
                text = await rows.nth(i).inner_text()
                lines.append(text.strip())
            return "\n".join(lines)

    except Exception:
        pass

    try:
        events = page.locator("div[data-test='event'], div.event, div.event-line, div.log-line, div.raw-event")
        count = await events.count()

        if count > 0:
            lines = []
            for i in range(min(count, max_lines)):
                text = await events.nth(i).inner_text()
                lines.append(text.strip())
            return "\n".join(lines)

    except Exception:
        pass

    try:
        container = page.locator(SPLUNK_RESULTS_CONTAINER).first
        text = await container.inner_text()
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        return "\n".join(lines[:max_lines])
    except Exception:
        pass

    return ""