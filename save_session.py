import asyncio
import json

from playwright.sync_api import sync_playwright

import config


def save_session():
    print(f"Opening browser to {config.SPLUNK_URL}")
    print("Complete the SSO login in the browser window.")
    print("Press Enter in this terminal when you are logged in and see the Splunk dashboard...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1280, "height": 720}, ignore_https_errors=True)
        page = context.new_page()

        page.goto(config.SPLUNK_URL)
        input("\n✅ Login completed? Press Enter to save session...")

        context.storage_state(path=config.SPLUNK_SESSION_PATH)

        print(f"\n✅ Session saved to {config.SPLUNK_SESSION_PATH}")
        print("You can now start the bot with: python main.py")

        browser.close()


if __name__ == "__main__":
    save_session()