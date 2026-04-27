#!/usr/bin/env python3
"""
查询 X List 的当前实际成员列表

用法:
  python3 list_members.py                  # 列出所有成员
  python3 list_members.py --check user1 user2  # 检查指定用户是否在 List 中
  python3 list_members.py --save           # 保存成员列表到 data/list_members.json
"""

import asyncio
import argparse
import json
from datetime import datetime
from pathlib import Path

import yaml
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).parent


def load_config():
    with open(BASE_DIR / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def fetch_list_members(list_url):
    """打开 List 成员页面，滚动抓取所有成员 username"""
    members_url = list_url.rstrip("/") + "/members"
    auth_file = BASE_DIR / "auth_state.json"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            storage_state=str(auth_file),
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        print(f"正在加载 List 成员页面: {members_url}")
        await page.goto(members_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        # 等待成员列表加载
        try:
            await page.wait_for_selector('[data-testid="UserCell"]', timeout=15000)
        except Exception:
            print("[!] 成员列表加载超时")
            await browser.close()
            return []

        # 滚动抓取所有成员（虚拟滚动，需要边滚边收集）
        # X 成员页面也用虚拟滚动，同时只有少量 UserCell 在 DOM 中
        members = set()
        stale_rounds = 0
        for round_num in range(300):
            # 尝试两种选择器：UserCell 和 通用的用户链接
            cells = await page.query_selector_all('[data-testid="UserCell"]')
            new_this_round = 0
            for cell in cells:
                # 从 UserCell 内提取用户名链接
                links = await cell.query_selector_all('a[role="link"][href]')
                for link in links:
                    href = await link.get_attribute("href") or ""
                    if not href.startswith("/"):
                        continue
                    stripped = href.strip("/")
                    # 用户名链接是单段路径（无 /），排除 i/ 开头的系统路径
                    if not stripped or "/" in stripped or stripped.startswith("i"):
                        continue
                    username = stripped.split("?")[0].lower()
                    if username not in members:
                        members.add(username)
                        new_this_round += 1
                        break  # 每个 cell 只取第一个用户名

            if new_this_round == 0:
                stale_rounds += 1
                if stale_rounds >= 10:
                    break
                await asyncio.sleep(1)
            else:
                stale_rounds = 0

            if round_num % 20 == 0 and round_num > 0:
                print(f"  ... 已发现 {len(members)} 个成员（第 {round_num} 轮）")

            # 每次滚动距离随机，模拟人类
            import random
            await page.mouse.wheel(0, random.randint(600, 1200))
            await asyncio.sleep(random.uniform(1.0, 2.0))

        await browser.close()

    return sorted(members)


async def main_async(args):
    config = load_config()
    list_url = config["lists"][0]

    members = await fetch_list_members(list_url)
    print(f"\nList 当前共 {len(members)} 个成员\n")

    if args.check:
        targets = [u.lstrip("@") for u in args.check]
        print(f"{'Account':<25} {'In List?'}")
        print("-" * 35)
        for u in targets:
            status = "YES" if u in members else "NO"
            print(f"@{u:<24} {status}")
    elif args.save:
        out = BASE_DIR / "data" / "list_members.json"
        out.write_text(json.dumps({
            "fetched_at": datetime.now().isoformat(),
            "count": len(members),
            "members": members,
        }, ensure_ascii=False, indent=2))
        print(f"已保存到 {out}")
    else:
        for m in members:
            print(f"  @{m}")


def main():
    parser = argparse.ArgumentParser(description="查询 X List 成员")
    parser.add_argument("--check", nargs="+", help="检查指定用户是否在 List 中")
    parser.add_argument("--save", action="store_true", help="保存到 JSON 文件")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
