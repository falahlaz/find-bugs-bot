import json
import os
import sys

from playwright.sync_api import sync_playwright

import config

OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "evidences", "sso_selectors.json")


def _build_selector(page, el) -> str:
    sel = el.get_attribute("id")
    if sel:
        return f"#{sel}"
    tag = el.evaluate("el => el.tagName")
    name = el.get_attribute("name")
    if name:
        return f"{tag.lower()}[name=\"{name}\"]"
    type_ = el.get_attribute("type")
    placeholder = el.get_attribute("placeholder") or ""
    text = el.inner_text().strip()[:30]
    if type_:
        return f"{tag.lower()}[type=\"{type_}\"]"
    if placeholder:
        return f"{tag.lower()}[placeholder*=\"{placeholder[:20]}\"]"
    if text:
        safe = text.replace('"', "&quot;")
        return f"{tag.lower()}:has-text(\"{safe}\")"
    return tag.lower()


def _dump_page(page, step_name: str) -> dict:
    data = {
        "step": step_name,
        "url": page.url,
        "title": page.title(),
        "inputs": [],
        "buttons": [],
        "forms": [],
    }

    for el in page.query_selector_all("input"):
        try:
            data["inputs"].append({
                "name": el.get_attribute("name") or "",
                "id": el.get_attribute("id") or "",
                "type": el.get_attribute("type") or "text",
                "placeholder": el.get_attribute("placeholder") or "",
                "value": el.get_attribute("value") or "",
                "selector": _build_selector(page, el),
            })
        except Exception:
            pass

    for el in page.query_selector_all("button"):
        try:
            data["buttons"].append({
                "text": el.inner_text().strip()[:50],
                "type": el.get_attribute("type") or "button",
                "selector": _build_selector(page, el),
            })
        except Exception:
            pass

    for el in page.query_selector_all("form"):
        try:
            data["forms"].append({
                "action": el.get_attribute("action") or "",
                "method": el.get_attribute("method") or "get",
                "selector": _build_selector(page, el),
            })
        except Exception:
            pass

    return data


def _print_page_report(page_data: dict):
    print(f"\n{'=' * 60}")
    print(f"STEP: {page_data['step']}")
    print(f"URL:  {page_data['url']}")
    print(f"TITLE: {page_data['title']}")

    if page_data["inputs"]:
        print(f"\n  INPUTS ({len(page_data['inputs'])}):")
        for inp in page_data["inputs"]:
            print(f"    selector : {inp['selector']}")
            print(f"    name     : {inp['name']}")
            print(f"    id       : {inp['id']}")
            print(f"    type     : {inp['type']}")
            print(f"    placeholder: {inp['placeholder']}")
            print()

    if page_data["buttons"]:
        print(f"  BUTTONS ({len(page_data['buttons'])}):")
        for btn in page_data["buttons"]:
            print(f"    selector: {btn['selector']}")
            print(f"    text    : {btn['text']}")
            print(f"    type    : {btn['type']}")
            print()

    if page_data["forms"]:
        print(f"  FORMS ({len(page_data['forms'])}):")
        for f in page_data["forms"]:
            print(f"    selector: {f['selector']}")
            print(f"    action  : {f['action']}")
            print(f"    method  : {f['method']}")
            print()

    print("=" * 60)


def run():
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    print(f"Opening browser to {config.SPLUNK_URL}")
    print("Walk through the SSO login flow in the browser.")
    print("Press Enter after each page transition. Type 'done' when fully logged in.\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            ignore_https_errors=True,
        )
        page = context.new_page()

        page.goto(config.SPLUNK_URL)

        all_pages = []
        step = 1

        while True:
            page.wait_for_load_state("networkidle", timeout=30000)
            data = _dump_page(page, f"step_{step}")
            all_pages.append(data)
            _print_page_report(data)

            cmd = input("\n[Enter = next page | 'done' = finish & save]: ").strip().lower()
            if cmd == "done":
                break
            step += 1

        context.storage_state(path=config.SPLUNK_SESSION_PATH)

        # Save selectors
        with open(OUTPUT_FILE, "w") as f:
            json.dump(all_pages, f, indent=2)
        print(f"\nSelectors saved to {OUTPUT_FILE}")
        print(f"Playwright session saved to {config.SPLUNK_SESSION_PATH}")

        browser.close()


if __name__ == "__main__":
    run()
