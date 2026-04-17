import asyncio
import json
import logging
import os
import sys
import time

from playwright.sync_api import sync_playwright

import config

logger = logging.getLogger(__name__)

REQUIRED_COOKIES = ("splunkd_8008", "session_id_8008", "splunkweb_csrf_token_8008", "token_key")


def auto_login() -> bool:
    missing = []
    if not config.SPLUNK_SSO_EMAIL:
        missing.append("SPLUNK_SSO_EMAIL")
    if not config.SPLUNK_SSO_EMPLOYEE_ID:
        missing.append("SPLUNK_SSO_EMPLOYEE_ID")
    if not config.SPLUNK_SSO_PASSWORD:
        missing.append("SPLUNK_SSO_PASSWORD")
    if missing:
        logger.error("Missing env vars: %s", ", ".join(missing))
        return False

    print(f"Opening browser to {config.SPLUNK_URL}")
    print("Auto-filling credentials. Approve 2FA push on your phone.\n")

    screenshot_dir = os.path.join(os.path.dirname(__file__), "evidences")
    os.makedirs(screenshot_dir, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            ignore_https_errors=True,
        )
        page = context.new_page()

        page.goto(config.SPLUNK_URL)

        # Step 1: Microsoft email page
        print("[Step 1] Waiting for Microsoft login page...")
        try:
            page.wait_for_selector("#i0116", timeout=30000)
        except Exception:
            _screenshot(page, screenshot_dir, "step1_email_page")
            logger.error("Step 1: #i0116 not found. URL=%s", page.url)
            browser.close()
            return False

        print(f"[Step 1] Current URL: {page.url}")
        email_input = page.locator("#i0116")
        email_input.click()
        email_input.fill("")
        email_input.press_sequentially(config.SPLUNK_SSO_EMAIL, delay=50)
        page.wait_for_timeout(300)
        print(f"[Step 1] Filled email: {config.SPLUNK_SSO_EMAIL}")

        page.click("#idSIButton9")
        print("[Step 1] Clicked Next. Waiting for SSO redirect...")

        try:
            page.wait_for_selector("#username", timeout=30000)
        except Exception:
            _screenshot(page, screenshot_dir, "step2_sso_page")
            logger.error("Step 2: #username not found. URL=%s", page.url)
            browser.close()
            return False

        # Step 2: Corporate SSO - employee ID + password
        print(f"[Step 2] Current URL: {page.url}")
        user_input = page.locator("#username")
        user_input.click()
        user_input.fill("")
        user_input.press_sequentially(config.SPLUNK_SSO_EMPLOYEE_ID, delay=50)
        page.wait_for_timeout(200)

        pw_input = page.locator("#password")
        pw_input.click()
        pw_input.fill("")
        pw_input.press_sequentially(config.SPLUNK_SSO_PASSWORD, delay=50)
        page.wait_for_timeout(200)
        print(f"[Step 2] Filled employee ID + password")

        page.click('input[type="submit"]')
        print("[Step 2] Clicked Login. Waiting for 2FA push...")

        # Step 3: Wait for "Stay signed in?" page (means 2FA approved)
        try:
            page.wait_for_selector("#idSIButton9", timeout=300000)
        except Exception:
            _screenshot(page, screenshot_dir, "step3_mfa_page")
            logger.error("Step 3: MFA timeout or 'Stay signed in' page not found. URL=%s", page.url)
            browser.close()
            return False

        print("[Step 3] 2FA approved. Clicking 'Stay signed in'...")
        page.click("#idSIButton9")
        print("[Step 3] Clicked Yes. Polling for session cookie...")

        # Step 4: Poll for session cookie
        if not _wait_for_cookie(context, "splunkd_8008", timeout=300):
            logger.error("Session cookie not found after 5 minutes")
            _screenshot(page, screenshot_dir, "step4_no_cookie")
            browser.close()
            return False

        print("Session cookie detected. Saving...")

        context.storage_state(path=config.SPLUNK_SESSION_PATH)
        print(f"\nPlaywright session saved to {config.SPLUNK_SESSION_PATH}")

        _extract_api_cookies(context)

        browser.close()

    return True


def _screenshot(page, out_dir: str, label: str):
    path = os.path.join(out_dir, f"sso_error_{label}_{int(time.time())}.png")
    try:
        page.screenshot(path=path)
        print(f"Screenshot saved: {path}")
    except Exception:
        pass


def _wait_for_cookie(context, cookie_name: str, timeout: int = 300) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        cookies = context.cookies()
        if any(c["name"] == cookie_name for c in cookies):
            return True
        time.sleep(2)
        print(".", end="", flush=True)
    return False


def _extract_api_cookies(context):
    cookies = context.cookies()
    api_session = {
        "base_url": config.SPLUNK_URL.rstrip("/"),
        "cookies": {},
        "csrf_token": None,
    }

    found = 0
    for cookie in cookies:
        name = cookie["name"]
        if name in REQUIRED_COOKIES:
            api_session["cookies"][name] = cookie["value"]
            if name == "splunkweb_csrf_token_8008":
                api_session["csrf_token"] = cookie["value"]
            found += 1

    if found < len(REQUIRED_COOKIES):
        print(f"\nWarning: Only found {found}/{len(REQUIRED_COOKIES)} required cookies.")
        print(f"  Required: {REQUIRED_COOKIES}")
        print(f"  Found: {list(api_session['cookies'].keys())}")
        print("  The API client may not work. Try logging in again.")
        return

    api_session_path = config.SPLUNK_API_SESSION_PATH
    with open(api_session_path, "w") as f:
        json.dump(api_session, f, indent=2)

    print(f"API session saved to {api_session_path}")
    print(f"  Cookies extracted: {list(api_session['cookies'].keys())}")
    print(f"  CSRF token: {api_session['csrf_token'][:20]}...")
    print("\nSession ready.")


async def auto_login_async() -> bool:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, auto_login)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    success = auto_login()
    sys.exit(0 if success else 1)
