#!/usr/bin/env python3
"""Screenshot X List members page while scrolling, save all screenshots."""
import asyncio
import random
from pathlib import Path
from playwright.async_api import async_playwright

AUTH_FILE = Path(__file__).parent / "auth_state.json"
URL = "https://x.com/i/lists/2026486577304842549/members"
SHOT_DIR = Path(__file__).parent / "data" / "member_screenshots"


async def main():
    SHOT_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            storage_state=str(AUTH_FILE),
            viewport={"width": 1280, "height": 3000},
        )
        page = await context.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(5)

        for i in range(40):
            path = SHOT_DIR / f"members_{i:03d}.png"
            await page.screenshot(path=str(path), full_page=False)
            print(f"截图 {i}: {path.name}", flush=True)
            # Scroll down
            await page.mouse.wheel(0, 2500)
            await asyncio.sleep(2)

        await browser.close()
    print(f"\n截图保存在 {SHOT_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
