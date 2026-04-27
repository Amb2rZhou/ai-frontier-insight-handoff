#!/usr/bin/env python3
"""
从 X List 中批量移除账号

用法:
  python3 batch_remove.py user1 user2 user3
  python3 batch_remove.py --dry-run user1 user2
"""

import asyncio
import argparse
import random
import sys
from pathlib import Path

import yaml
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).parent


def load_config():
    with open(BASE_DIR / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def remove_from_list(context, username, list_name="高质量科技博主"):
    """将一个用户从指定 List 移除"""
    page = await context.new_page()
    success = False

    try:
        await page.goto(
            f"https://x.com/{username}",
            wait_until="domcontentloaded",
            timeout=15000,
        )
        await asyncio.sleep(random.uniform(2, 4))

        # 检查账号是否存在
        suspended = await page.query_selector('[data-testid="emptyState"]')
        if suspended:
            print(f"    [!] 账号不存在或已被封禁")
            return False

        # 点击 "..." 更多按钮
        more_btn = await page.query_selector('[data-testid="userActions"]')
        if not more_btn:
            print(f"    [!] 找不到操作按钮")
            return False

        await more_btn.click()
        await asyncio.sleep(random.uniform(1, 2))

        # 点击 "Add/remove from Lists"
        menu_items = await page.query_selector_all('[role="menuitem"]')
        clicked = False
        for item in menu_items:
            text = await item.inner_text()
            if "List" in text and ("Add" in text or "remove" in text):
                await item.click()
                clicked = True
                break

        if not clicked:
            print(f"    [!] 找不到 List 菜单项")
            await page.keyboard.press("Escape")
            return False

        # 等待列表弹窗加载
        try:
            await page.wait_for_selector(
                '[data-testid="listCell"]', timeout=8000
            )
        except Exception:
            print(f"    [!] 列表加载超时")
            await page.keyboard.press("Escape")
            return False

        await asyncio.sleep(0.5)

        # 找到目标 List，如果已勾选则取消勾选
        checkboxes = await page.query_selector_all('[data-testid="listCell"]')
        for cb in checkboxes:
            cb_text = await cb.inner_text()
            if list_name in cb_text:
                is_checked = await cb.get_attribute("aria-checked")
                if is_checked == "true":
                    await cb.click()
                    await asyncio.sleep(0.5)
                    success = True
                else:
                    print(f"    [!] 未在此 List 中")
                    success = True  # 已经不在了，也算成功
                break

        # 保存
        save_btn = await page.query_selector(
            '[data-testid="listSelector"] [role="button"]'
        )
        if not save_btn:
            buttons = await page.query_selector_all('[role="button"]')
            for btn in buttons:
                text = await btn.inner_text()
                if text.strip() == "Save":
                    save_btn = btn
                    break

        if save_btn:
            await save_btn.click()
            await asyncio.sleep(1)
        else:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.5)

    except Exception as e:
        print(f"    [!] 错误: {e}")
        success = False
    finally:
        await page.close()

    return success


async def run_remove(usernames, dry_run=False):
    """批量移除"""
    print(f"准备从 List 移除 {len(usernames)} 个账号:")
    for u in usernames:
        print(f"  @{u}")

    if dry_run:
        print("\n[预览模式] 不会实际执行")
        return

    auth_file = BASE_DIR / "auth_state.json"
    if not auth_file.exists():
        print("[!] 未找到登录会话")
        sys.exit(1)

    print()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            storage_state=str(auth_file),
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )

        ok_count = 0
        for i, username in enumerate(usernames):
            print(f"[{i+1}/{len(usernames)}] @{username} ...", end=" ")
            ok = await remove_from_list(context, username)
            if ok:
                print("已移除")
                ok_count += 1
            else:
                print("失败")

            if i < len(usernames) - 1:
                delay = random.uniform(5, 12)
                await asyncio.sleep(delay)

        await browser.close()

    print(f"\n完成: {ok_count}/{len(usernames)} 成功移除")


def main():
    parser = argparse.ArgumentParser(description="从 X List 批量移除账号")
    parser.add_argument("usernames", nargs="+", help="要移除的用户名")
    parser.add_argument("--dry-run", action="store_true", help="预览，不实际执行")
    args = parser.parse_args()

    # 去掉 @ 前缀
    usernames = [u.lstrip("@") for u in args.usernames]
    asyncio.run(run_remove(usernames, args.dry_run))


if __name__ == "__main__":
    main()
