from __future__ import annotations

from playwright.async_api import Browser, Error, Playwright


async def launch_browser(playwright: Playwright, *, headless: bool) -> Browser:
    """Launch from ``playwright`` and return a browser, optionally without visible UI."""
    try:
        return await playwright.chromium.launch(headless=headless)
    except Error as error:
        if "Executable doesn't exist" not in str(error):
            raise
        return await playwright.chromium.launch(channel="chrome", headless=headless)
