import os
import logging

from playwright.async_api import async_playwright, Browser, BrowserContext

import config

logger = logging.getLogger(__name__)


class BrowserManager:
    def __init__(self):
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.session_file_path: str | None = None
        self._restart_attempts = 0
        self._max_restarts = 1

    async def start(self):
        self._playwright = await async_playwright().start()
        await self._launch_browser()
        logger.info("Browser manager started")

    async def _launch_browser(self):
        session_path = config.SPLUNK_SESSION_PATH
        self.session_file_path = session_path

        self._browser = await self._playwright.chromium.launch(headless=True)
        storage_state = None
        if os.path.exists(session_path):
            storage_state = session_path
            logger.info("Loaded session from %s", session_path)
        else:
            logger.error("Session file %s not found. Run 'python save_session.py' first.", session_path)

        self._context = await self._browser.new_context(
            storage_state=storage_state,
            viewport={"width": 1280, "height": 720},
        )
        self._restart_attempts = 0

    async def get_page(self):
        if self._context is None:
            raise RuntimeError("Browser context not initialized")
        return await self._context.new_page()

    async def restart(self) -> bool:
        logger.warning("Attempting browser restart...")
        try:
            await self.close_context()
            await self._launch_browser()
            logger.info("Browser restarted successfully")
            self._restart_attempts += 1
            return True
        except Exception:
            logger.exception("Browser restart failed")
            return False

    async def close_context(self):
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass

    async def close(self):
        await self.close_context()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser manager stopped")

    @property
    def restart_count(self) -> int:
        return self._restart_attempts

    @property
    def can_restart(self) -> bool:
        return self._restart_attempts < self._max_restarts


browser_manager = BrowserManager()