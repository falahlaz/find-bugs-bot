import asyncio
import json
import os

from playwright.sync_api import sync_playwright

import config

REQUIRED_COOKIES = ("splunkd_8008", "session_id_8008", "splunkweb_csrf_token_8008", "token_key")


def save_session():
    print(f"Opening browser to {config.SPLUNK_URL}")
    print("Complete the SSO login in the browser window.")
    print("Press Enter in this terminal when you are logged in and see the Splunk dashboard...\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            ignore_https_errors=True,
        )
        page = context.new_page()

        page.goto(config.SPLUNK_URL)
        input("\nLogin completed? Press Enter to save session...")

        context.storage_state(path=config.SPLUNK_SESSION_PATH)
        print(f"\nPlaywright session saved to {config.SPLUNK_SESSION_PATH}")

        _extract_api_cookies(context)

        browser.close()


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
    print("\nYou can now start the bot with: python main.py")


if __name__ == "__main__":
    save_session()