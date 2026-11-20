
from playwright.sync_api import sync_playwright

with sync_playwright() as p:

    browser = p.chromium.connect_over_cdp(
        "http://127.0.0.1:9222"
    )

    print("Chrome connected successfully!")

    for context in browser.contexts:

        for page in context.pages:

            print("URL:", page.url)
            print("Title:", page.title())

    print("Playwright test completed.")
