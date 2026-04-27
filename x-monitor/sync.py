#!/usr/bin/env python3
"""
X List 自动同步脚本
从 x.mitbunny.ai 获取推荐的 AI 博主，自动维护 X List 成员。

用法:
  初始化基线:    python3 sync.py --init       (读取当前 List 成员作为基线)
  查看推荐:      python3 sync.py --fetch      (抓取 mitbunny 推荐，显示差异)
  预览变更:      python3 sync.py --dry-run    (显示将要执行的增删操作)
  执行同步:      python3 sync.py --sync       (实际执行增删操作)
"""

import asyncio
import argparse
import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# 本地状态文件
MITBUNNY_CACHE = DATA_DIR / "mitbunny.json"       # mitbunny 抓取缓存
LIST_MEMBERS   = DATA_DIR / "list_members.json"    # 当前 List 成员
SYNC_LOG       = DATA_DIR / "sync_log.json"        # 同步历史记录


def load_config():
    with open(BASE_DIR / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_json(path: Path, default=None):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default if default is not None else []


def save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_followers(text: str) -> int:
    """解析粉丝数文本: '4.6M' -> 4600000, '134K' -> 134000"""
    text = text.strip().upper()
    try:
        if "M" in text:
            return int(float(text.replace("M", "")) * 1_000_000)
        elif "K" in text:
            return int(float(text.replace("K", "")) * 1_000)
        else:
            return int(text.replace(",", ""))
    except ValueError:
        return 0


# ─── 抓取 mitbunny ───────────────────────────────────────────

async def fetch_mitbunny(source_url: str, headless=True):
    """抓取 x.mitbunny.ai 的账号列表"""
    print("正在抓取 x.mitbunny.ai ...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        page = await browser.new_page()
        await page.goto(source_url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)

        accounts = []
        seen_handles = set()

        # 找到左侧列表容器并持续滚动
        sidebar = await page.query_selector('[class*="sidebar"]') or page
        # 尝试多种选择器找到滚动容器
        scroll_container = (
            await page.query_selector('[class*="list"]')
            or await page.query_selector('[class*="sidebar"]')
            or await page.query_selector('[class*="scroll"]')
            or page
        )

        prev_count = 0
        stale_rounds = 0

        for round_num in range(50):  # 最多滚动 50 轮
            # 提取当前可见的条目
            # 每个条目结构: 排名、名字 @handle、角色、粉丝数
            items = await page.query_selector_all('[class*="item"], [class*="entry"], [class*="row"], [class*="card"]')

            if not items:
                # 尝试更通用的方式：查找包含 @ 的文本元素
                all_text = await page.evaluate('''() => {
                    const results = [];
                    const walker = document.createTreeWalker(
                        document.body, NodeFilter.SHOW_TEXT, null, false
                    );
                    let node;
                    while (node = walker.nextNode()) {
                        if (node.textContent.includes("@")) {
                            const parent = node.parentElement;
                            if (parent) {
                                const rect = parent.getBoundingClientRect();
                                if (rect.left < 350) {  // 左侧面板
                                    results.push({
                                        text: parent.closest("[class]")?.innerText || parent.innerText,
                                        y: rect.top
                                    });
                                }
                            }
                        }
                    }
                    return results;
                }''')

            # 更可靠的方式：直接从页面提取所有包含 @ 的文本块
            entries = await page.evaluate('''() => {
                const entries = [];
                // 查找左侧列表中的所有条目
                const elements = document.querySelectorAll('a, div, li');
                for (const el of elements) {
                    const text = el.innerText || "";
                    const rect = el.getBoundingClientRect();
                    // 只看左侧面板 (x < 350) 且包含 @ 符号
                    if (rect.left < 350 && rect.width > 100 && rect.width < 400
                        && text.includes("@") && text.length < 200) {
                        // 尝试提取 handle
                        const handleMatch = text.match(/@(\\w+)/);
                        if (handleMatch) {
                            entries.push({
                                text: text.trim(),
                                handle: handleMatch[1],
                                y: rect.top
                            });
                        }
                    }
                }
                return entries;
            }''')

            for entry in entries:
                handle = entry["handle"].lower()
                if handle in seen_handles or handle == "jenny_the_bunny":
                    continue
                seen_handles.add(handle)

                # 从文本中解析信息
                text = entry["text"]
                lines = [l.strip() for l in text.split("\n") if l.strip()]

                name = ""
                role = ""
                followers = 0

                for line in lines:
                    if "@" in line:
                        # 这行包含名字和 handle
                        name = re.sub(r"\s*@\w+\s*", "", line).strip()
                    elif any(c in line for c in ["M", "K"]) and len(line) < 10:
                        followers = parse_followers(line)
                    elif not line.isdigit() and len(line) > 3:
                        role = line

                accounts.append({
                    "handle": entry["handle"],
                    "name": name or entry["handle"],
                    "role": role,
                    "followers": followers,
                })

            if len(seen_handles) == prev_count:
                stale_rounds += 1
                if stale_rounds >= 3:
                    break
            else:
                stale_rounds = 0
            prev_count = len(seen_handles)

            # 滚动左侧面板
            await page.evaluate('''() => {
                // 尝试找到可滚动的左侧容器
                const containers = document.querySelectorAll('div, nav, aside, section');
                for (const c of containers) {
                    const rect = c.getBoundingClientRect();
                    if (rect.left < 50 && rect.width < 400 && rect.width > 200
                        && c.scrollHeight > c.clientHeight) {
                        c.scrollTop += 500;
                        return;
                    }
                }
                // 回退: 滚动整个页面
                window.scrollBy(0, 500);
            }''')
            await asyncio.sleep(0.8)

        await browser.close()

    print(f"  抓取到 {len(accounts)} 个账号")
    return accounts


# ─── 读取 X List 当前成员 ─────────────────────────────────────

async def fetch_list_members(list_id: str, headless=True):
    """从 X List 成员页面抓取当前成员"""
    print("正在读取 X List 当前成员...")
    auth_file = BASE_DIR / "auth_state.json"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            storage_state=str(auth_file),
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()
        url = f"https://x.com/i/lists/{list_id}/members"
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        members = set()
        prev_count = 0
        stale_rounds = 0

        for _ in range(30):
            # 提取成员的 handle
            handles = await page.evaluate('''() => {
                const links = document.querySelectorAll('a[href^="/"]');
                const handles = new Set();
                for (const a of links) {
                    const href = a.getAttribute("href");
                    if (href && href.match(/^\\/\\w+$/) && !href.includes("/")
                        && href !== "/home" && href !== "/explore"
                        && href !== "/notifications" && href !== "/messages") {
                        handles.add(href.slice(1).toLowerCase());
                    }
                }
                return [...handles];
            }''')

            for h in handles:
                members.add(h)

            if len(members) == prev_count:
                stale_rounds += 1
                if stale_rounds >= 3:
                    break
            else:
                stale_rounds = 0
            prev_count = len(members)

            await page.mouse.wheel(0, 500)
            await asyncio.sleep(1)

        await browser.close()

    # 过滤掉常见的导航链接
    nav_items = {"home", "explore", "search", "notifications", "messages",
                 "bookmarks", "lists", "profile", "more", "settings",
                 "compose", "i", "premium", "communities", "grok"}
    members = members - nav_items

    print(f"  当前 List 有 {len(members)} 个成员")
    return sorted(members)


# ─── X List 成员增删 ──────────────────────────────────────────

async def modify_list_members(list_id, to_add, to_remove, config):
    """通过 Playwright 操作 X 页面，添加/移除 List 成员"""
    sync_cfg = config["sync"]
    auth_file = BASE_DIR / "auth_state.json"
    delay_min = sync_cfg.get("action_delay_min", 5)
    delay_max = sync_cfg.get("action_delay_max", 12)

    if not to_add and not to_remove:
        print("  无需变更")
        return [], []

    added = []
    removed = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # 可视模式，方便观察
        context = await browser.new_context(
            storage_state=str(auth_file),
            viewport={"width": 1280, "height": 800},
        )

        # ── 添加成员 ──
        for username in to_add:
            try:
                page = await context.new_page()
                print(f"  添加 @{username} ...")
                await page.goto(
                    f"https://x.com/{username}",
                    wait_until="domcontentloaded",
                    timeout=15000,
                )
                await asyncio.sleep(2)

                # 点击 "..." 更多按钮
                more_btn = await page.query_selector(
                    '[data-testid="userActions"]'
                )
                if not more_btn:
                    print(f"    [!] 找不到操作按钮，跳过")
                    await page.close()
                    continue
                await more_btn.click()
                await asyncio.sleep(1)

                # 点击 "Add/remove from Lists"
                list_option = await page.query_selector(
                    '[data-testid="addOrRemoveFromLists"]'
                )
                if not list_option:
                    # 尝试通过文本匹配
                    menu_items = await page.query_selector_all('[role="menuitem"]')
                    for item in menu_items:
                        text = await item.inner_text()
                        if "List" in text or "列表" in text:
                            list_option = item
                            break

                if not list_option:
                    print(f"    [!] 找不到 List 选项，跳过")
                    await page.close()
                    continue

                await list_option.click()
                await asyncio.sleep(2)

                # 在弹出的 List 选择框中勾选目标 List
                # 查找包含 list_id 的复选框或列表项
                checkboxes = await page.query_selector_all(
                    '[role="checkbox"], [data-testid*="list"]'
                )
                for cb in checkboxes:
                    # 点击我们的目标 List
                    parent_text = await cb.evaluate(
                        'el => el.closest("[class]")?.innerText || ""'
                    )
                    if "高质量科技博主" in parent_text:
                        is_checked = await cb.get_attribute("aria-checked")
                        if is_checked != "true":
                            await cb.click()
                            await asyncio.sleep(1)
                            print(f"    已添加")
                            added.append(username)
                        else:
                            print(f"    已在列表中")
                        break
                else:
                    # 尝试点击包含 List 名称的元素
                    list_items = await page.query_selector_all('span')
                    for item in list_items:
                        text = await item.inner_text()
                        if "高质量科技博主" in text:
                            await item.click()
                            await asyncio.sleep(1)
                            added.append(username)
                            print(f"    已添加")
                            break

                await page.close()

            except Exception as e:
                print(f"    [!] 操作失败: {e}")
                try:
                    await page.close()
                except Exception:
                    pass

            # 人类式延迟
            delay = random.uniform(delay_min, delay_max)
            await asyncio.sleep(delay)

        # ── 移除成员 ──
        for username in to_remove:
            try:
                page = await context.new_page()
                print(f"  移除 @{username} ...")
                await page.goto(
                    f"https://x.com/{username}",
                    wait_until="domcontentloaded",
                    timeout=15000,
                )
                await asyncio.sleep(2)

                more_btn = await page.query_selector(
                    '[data-testid="userActions"]'
                )
                if not more_btn:
                    print(f"    [!] 找不到操作按钮，跳过")
                    await page.close()
                    continue
                await more_btn.click()
                await asyncio.sleep(1)

                list_option = await page.query_selector(
                    '[data-testid="addOrRemoveFromLists"]'
                )
                if not list_option:
                    menu_items = await page.query_selector_all('[role="menuitem"]')
                    for item in menu_items:
                        text = await item.inner_text()
                        if "List" in text or "列表" in text:
                            list_option = item
                            break

                if list_option:
                    await list_option.click()
                    await asyncio.sleep(2)

                    checkboxes = await page.query_selector_all(
                        '[role="checkbox"], [data-testid*="list"]'
                    )
                    for cb in checkboxes:
                        parent_text = await cb.evaluate(
                            'el => el.closest("[class]")?.innerText || ""'
                        )
                        if "高质量科技博主" in parent_text:
                            is_checked = await cb.get_attribute("aria-checked")
                            if is_checked == "true":
                                await cb.click()
                                await asyncio.sleep(1)
                                removed.append(username)
                                print(f"    已移除")
                            break

                await page.close()

            except Exception as e:
                print(f"    [!] 操作失败: {e}")
                try:
                    await page.close()
                except Exception:
                    pass

            delay = random.uniform(delay_min, delay_max)
            await asyncio.sleep(delay)

        await browser.close()

    return added, removed


# ─── 筛选逻辑 ─────────────────────────────────────────────────

def filter_accounts(accounts, sync_cfg):
    """根据配置筛选账号"""
    keywords = [k.lower() for k in sync_cfg.get("role_keywords", [])]
    min_followers = sync_cfg.get("min_followers", 0)
    max_members = sync_cfg.get("max_members", 200)

    filtered = []
    for acc in accounts:
        # 粉丝数过滤
        if acc["followers"] < min_followers:
            continue

        # 角色关键词过滤（如果配置了关键词）
        if keywords:
            role_lower = acc["role"].lower()
            if not any(kw in role_lower for kw in keywords):
                continue

        filtered.append(acc)

    # 按粉丝数排序，取前 max_members 个
    filtered.sort(key=lambda x: x["followers"], reverse=True)
    return filtered[:max_members]


# ─── 差异计算 ─────────────────────────────────────────────────

def compute_diff(desired_handles, current_members):
    """计算需要添加和移除的账号"""
    desired = set(h.lower() for h in desired_handles)
    current = set(h.lower() for h in current_members)

    to_add = desired - current
    to_remove = current - desired

    return sorted(to_add), sorted(to_remove)


# ─── 命令: init ───────────────────────────────────────────────

async def cmd_init(config):
    """初始化：读取当前 X List 成员作为基线"""
    list_id = config["sync"]["list_id"]
    members = await fetch_list_members(list_id)
    save_json(LIST_MEMBERS, members)
    print(f"\n基线已保存，当前 {len(members)} 个成员:")
    for m in members:
        print(f"  @{m}")


# ─── 命令: fetch ──────────────────────────────────────────────

async def cmd_fetch(config):
    """抓取 mitbunny 推荐，显示与当前 List 的差异"""
    sync_cfg = config["sync"]
    source_url = sync_cfg["source_url"]

    # 抓取 mitbunny
    accounts = await fetch_mitbunny(source_url)
    save_json(MITBUNNY_CACHE, accounts)

    # 筛选
    filtered = filter_accounts(accounts, sync_cfg)
    print(f"\n筛选后 {len(filtered)} 个账号符合条件")

    # 加载当前成员
    current = load_json(LIST_MEMBERS, [])
    if not current:
        print("[!] 未找到基线数据，请先运行: python3 sync.py --init")
        return

    # 计算差异
    desired_handles = [a["handle"] for a in filtered]
    to_add, to_remove = compute_diff(desired_handles, current)

    print(f"\n─── 差异分析 ───")
    print(f"当前 List 成员: {len(current)}")
    print(f"推荐账号:       {len(filtered)}")

    if to_add:
        print(f"\n待添加 ({len(to_add)}):")
        for h in to_add:
            acc = next((a for a in filtered if a["handle"].lower() == h), None)
            if acc:
                print(f"  + @{acc['handle']:20s} {acc['role']:30s} {acc['followers']:>10,}")
            else:
                print(f"  + @{h}")

    if to_remove:
        print(f"\n待移除 ({len(to_remove)}):")
        for h in to_remove:
            print(f"  - @{h}")

    if not to_add and not to_remove:
        print("\n已完全同步，无需变更")


# ─── 命令: sync ───────────────────────────────────────────────

async def cmd_sync(config, dry_run=False):
    """执行完整同步"""
    sync_cfg = config["sync"]
    source_url = sync_cfg["source_url"]
    max_add = sync_cfg.get("max_additions_per_run", 10)
    max_remove = sync_cfg.get("max_removals_per_run", 5)

    # 抓取 mitbunny
    accounts = await fetch_mitbunny(source_url)
    save_json(MITBUNNY_CACHE, accounts)

    # 筛选
    filtered = filter_accounts(accounts, sync_cfg)
    print(f"筛选后 {len(filtered)} 个账号符合条件")

    # 加载当前成员
    current = load_json(LIST_MEMBERS, [])
    if not current:
        print("[!] 未找到基线数据，请先运行: python3 sync.py --init")
        return

    # 计算差异
    desired_handles = [a["handle"] for a in filtered]
    to_add, to_remove = compute_diff(desired_handles, current)

    # 限制每次操作数量
    to_add = to_add[:max_add]
    to_remove = to_remove[:max_remove]

    print(f"\n本次将添加 {len(to_add)} 个，移除 {len(to_remove)} 个")

    if to_add:
        print("\n待添加:")
        for h in to_add:
            acc = next((a for a in filtered if a["handle"].lower() == h), None)
            role = acc["role"] if acc else ""
            print(f"  + @{h:20s} {role}")

    if to_remove:
        print("\n待移除:")
        for h in to_remove:
            print(f"  - @{h}")

    if not to_add and not to_remove:
        print("\n已完全同步，无需变更")
        return

    if dry_run:
        print("\n[预览模式] 以上为计划变更，未实际执行")
        return

    # 执行变更
    print(f"\n{'=' * 50}")
    print("开始执行变更...")
    added, removed = await modify_list_members(
        sync_cfg["list_id"], to_add, to_remove, config
    )

    # 更新本地成员列表
    current_set = set(current)
    for h in added:
        current_set.add(h.lower())
    for h in removed:
        current_set.discard(h.lower())
    save_json(LIST_MEMBERS, sorted(current_set))

    # 记录同步日志
    log = load_json(SYNC_LOG, [])
    log.append({
        "time": datetime.now().isoformat(),
        "added": added,
        "removed": removed,
        "total_members": len(current_set),
    })
    save_json(SYNC_LOG, log[-50:])  # 只保留最近 50 条

    print(f"\n{'=' * 50}")
    print(f"同步完成: 添加 {len(added)}, 移除 {len(removed)}")
    print(f"List 当前共 {len(current_set)} 个成员")


# ─── 入口 ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="X List 自动同步工具")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--init", action="store_true",
        help="读取当前 List 成员作为基线",
    )
    group.add_argument(
        "--fetch", action="store_true",
        help="抓取 mitbunny 推荐，显示差异",
    )
    group.add_argument(
        "--dry-run", action="store_true",
        help="预览同步变更（不实际执行）",
    )
    group.add_argument(
        "--sync", action="store_true",
        help="执行同步（添加/移除 List 成员）",
    )
    args = parser.parse_args()

    config = load_config()

    if args.init:
        asyncio.run(cmd_init(config))
    elif args.fetch:
        asyncio.run(cmd_fetch(config))
    elif args.dry_run:
        asyncio.run(cmd_sync(config, dry_run=True))
    elif args.sync:
        asyncio.run(cmd_sync(config, dry_run=False))


if __name__ == "__main__":
    main()
