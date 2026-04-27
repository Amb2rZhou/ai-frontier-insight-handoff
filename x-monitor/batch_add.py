#!/usr/bin/env python3
"""
分批添加账号到 X List
每次运行添加一批（默认 30 个），从上次进度继续。

用法:
  python3 batch_add.py              # 添加下一批（30 个）
  python3 batch_add.py --batch 20   # 添加下一批（20 个）
  python3 batch_add.py --status     # 查看当前进度
  python3 batch_add.py --dry-run    # 预览下一批，不实际执行
"""

import asyncio
import argparse
import json
import random
import sys
from datetime import datetime
from pathlib import Path

import yaml
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
PROGRESS_FILE = DATA_DIR / "batch_progress.json"
HANDLES_FILE = DATA_DIR / "initial_handles.txt"


def load_config():
    with open(BASE_DIR / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_progress():
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    return {"completed": [], "failed": [], "next_index": 0}


def save_progress(progress):
    PROGRESS_FILE.write_text(
        json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_handles():
    if not HANDLES_FILE.exists():
        print(f"[!] 未找到 {HANDLES_FILE}")
        print("    请先运行 python3 sync.py --fetch 生成账号列表")
        sys.exit(1)
    return [h.strip() for h in HANDLES_FILE.read_text().splitlines() if h.strip()]


async def add_to_list(context, username, list_name="高质量科技博主"):
    """将一个用户添加到指定 List"""
    page = await context.new_page()
    success = False

    try:
        await page.goto(
            f"https://x.com/{username}",
            wait_until="domcontentloaded",
            timeout=15000,
        )
        await asyncio.sleep(random.uniform(2, 4))

        # 检查页面是否正常（账号可能不存在或被封）
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

        # 等待 "Pick a List" 弹窗内列表加载完成
        try:
            await page.wait_for_selector(
                '[data-testid="listCell"]', timeout=8000
            )
        except Exception:
            print(f"    [!] 列表加载超时")
            await page.keyboard.press("Escape")
            return False

        await asyncio.sleep(0.5)

        # 找到目标 List 的 checkbox 并勾选
        checkboxes = await page.query_selector_all('[data-testid="listCell"]')
        for cb in checkboxes:
            cb_text = await cb.inner_text()
            if list_name in cb_text:
                is_checked = await cb.get_attribute("aria-checked")
                if is_checked != "true":
                    await cb.click()
                    await asyncio.sleep(0.5)
                success = True
                break

        if not success:
            print(f"    [!] 未找到目标 List")
            await page.keyboard.press("Escape")
            return False

        # 点击 Save 按钮保存
        save_btn = await page.query_selector(
            '[data-testid="listSelector"] [role="button"]'
        )
        if not save_btn:
            # 备选: 查找文字为 Save 的按钮
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
            # 如果找不到 Save，按 Escape 可能会自动保存
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.5)

    except Exception as e:
        print(f"    [!] 错误: {e}")
        success = False
    finally:
        await page.close()

    return success


async def run_batch(batch_size=30, dry_run=False):
    """执行一批添加操作"""
    config = load_config()
    handles = load_handles()
    progress = load_progress()
    start_idx = progress["next_index"]

    if start_idx >= len(handles):
        print("所有账号已添加完毕!")
        print(f"  成功: {len(progress['completed'])}")
        print(f"  失败: {len(progress['failed'])}")
        return

    batch = handles[start_idx : start_idx + batch_size]
    remaining = len(handles) - start_idx

    print(f"{'=' * 50}")
    print(f"批量添加 - 第 {start_idx // batch_size + 1} 批")
    print(f"{'=' * 50}")
    print(f"总进度: {start_idx}/{len(handles)} ({start_idx * 100 // len(handles)}%)")
    print(f"本批: {len(batch)} 个，剩余: {remaining} 个")
    print(f"预计还需 {(remaining + batch_size - 1) // batch_size} 批完成")
    print(f"-" * 50)

    if dry_run:
        print("\n[预览模式] 本批将添加:")
        for i, h in enumerate(batch, 1):
            print(f"  {start_idx + i:3d}. @{h}")
        return

    auth_file = BASE_DIR / "auth_state.json"
    if not auth_file.exists():
        print("[!] 未找到登录会话，请先设置 cookies")
        sys.exit(1)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            storage_state=str(auth_file),
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )

        for i, handle in enumerate(batch):
            idx = start_idx + i + 1
            print(f"[{idx}/{len(handles)}] @{handle} ...", end=" ")

            ok = await add_to_list(context, handle)

            if ok:
                print("OK")
                progress["completed"].append(handle)
            else:
                print("SKIP")
                progress["failed"].append(handle)

            progress["next_index"] = start_idx + i + 1
            save_progress(progress)

            # 人类式随机延迟
            if i < len(batch) - 1:
                delay = random.uniform(5, 12)
                await asyncio.sleep(delay)

        await browser.close()

    print(f"\n{'=' * 50}")
    print(f"本批完成!")
    succeeded = sum(1 for h in batch if h in progress["completed"])
    print(f"  成功: {succeeded}/{len(batch)}")
    print(f"  总进度: {progress['next_index']}/{len(handles)}")

    if progress["next_index"] < len(handles):
        next_batch = min(batch_size, len(handles) - progress["next_index"])
        print(f"\n下次运行将添加接下来的 {next_batch} 个账号")


def show_status():
    """显示当前进度"""
    handles = load_handles()
    progress = load_progress()

    print(f"总账号数:   {len(handles)}")
    print(f"已处理:     {progress['next_index']}")
    print(f"  成功:     {len(progress['completed'])}")
    print(f"  失败:     {len(progress['failed'])}")
    print(f"剩余:       {len(handles) - progress['next_index']}")
    print(f"进度:       {progress['next_index'] * 100 // len(handles)}%")

    if progress["failed"]:
        print(f"\n失败的账号:")
        for h in progress["failed"]:
            print(f"  @{h}")


def main():
    parser = argparse.ArgumentParser(description="分批添加账号到 X List")
    parser.add_argument(
        "--batch", type=int, default=30, help="每批添加数量（默认 30）"
    )
    parser.add_argument(
        "--status", action="store_true", help="查看当前进度"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="预览下一批，不实际执行"
    )
    parser.add_argument(
        "--reset", action="store_true", help="重置进度，从头开始"
    )
    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.reset:
        if PROGRESS_FILE.exists():
            PROGRESS_FILE.unlink()
        print("进度已重置")
    elif args.dry_run:
        asyncio.run(run_batch(args.batch, dry_run=True))
    else:
        asyncio.run(run_batch(args.batch))


if __name__ == "__main__":
    main()
