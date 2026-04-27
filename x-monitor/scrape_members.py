#!/usr/bin/env python3
"""Scrape all X List members with improved scrolling."""
import asyncio
import json
import random
from pathlib import Path
from playwright.async_api import async_playwright

AUTH_FILE = Path(__file__).parent / "auth_state.json"
URL = "https://x.com/i/lists/2026486577304842549/members"
OUTPUT = Path(__file__).parent / "data" / "actual_list_members.json"

JS_EXTRACT = """
() => {
    const cells = document.querySelectorAll('[data-testid="UserCell"]');
    const usernames = [];
    cells.forEach(cell => {
        const links = cell.querySelectorAll('a[role="link"]');
        for (const link of links) {
            const href = link.getAttribute("href") || "";
            if (!href.startsWith("/")) continue;
            const stripped = href.replace(/^\\//, "").split("?")[0];
            if (!stripped || stripped.includes("/") || stripped.startsWith("i")) continue;
            usernames.push(stripped.toLowerCase());
            break;
        }
    });
    return usernames;
}
"""

JS_SCROLL = """
() => {
    window.scrollBy(0, 800);
}
"""

JS_BIG_SCROLL = """
() => {
    window.scrollBy(0, 3000);
}
"""


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            storage_state=str(AUTH_FILE),
            viewport={"width": 1280, "height": 2000},
        )
        page = await context.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(5)

        try:
            await page.wait_for_selector('[data-testid="UserCell"]', timeout=15000)
        except Exception:
            print("加载超时")
            await browser.close()
            return

        members = set()
        stale = 0

        for r in range(600):
            new_members = await page.evaluate(JS_EXTRACT)
            new_count = 0
            for u in new_members:
                if u not in members:
                    members.add(u)
                    new_count += 1

            if new_count == 0:
                stale += 1
                if stale >= 30:
                    break
            else:
                stale = 0

            if r % 50 == 0:
                print(f"  轮次 {r}: 已发现 {len(members)} 个成员", flush=True)

            await page.evaluate(JS_SCROLL)
            await asyncio.sleep(random.uniform(0.5, 1.0))

            if r % 5 == 0:
                await page.evaluate(JS_BIG_SCROLL)
                await asyncio.sleep(1)

        await browser.close()

    sorted_members = sorted(members)
    print(f"\n实际 List 成员: {len(sorted_members)} 个\n")
    for m in sorted_members:
        print(f"  @{m}")

    with open(OUTPUT, "w") as f:
        json.dump(sorted_members, f, indent=2)
    print(f"\n已保存到 {OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
