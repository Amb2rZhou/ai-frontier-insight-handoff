#!/usr/bin/env python3
"""
X (Twitter) List 监控脚本
通过 X List 页面一次性抓取多个账号的推文，保存到本地文件。
只需加载一个页面，像正常用户浏览 List 一样，安全高效。

用法:
  设置登录:  python3 monitor.py --login --auth-token <值> --ct0 <值>
  单次监控:  python3 monitor.py
  持续监控:  python3 monitor.py --loop
  自定间隔:  python3 monitor.py --loop --interval 600
  流水线模式: python3 monitor.py --pipeline
  指定输出:  python3 monitor.py --pipeline --pipeline-output /tmp/x-tweets.json
"""

import asyncio
import argparse
import hashlib
import json
import os
import random
import re
import sys
import urllib.parse
from datetime import datetime, date, timezone
from pathlib import Path

import yaml
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

BASE_DIR = Path(__file__).parent


# ─── 配置加载 ───────────────────────────────────────────────

def load_config(path="config.yaml"):
    with open(BASE_DIR / path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ─── 已读记录管理 ─────────────────────────────────────────────

def load_seen(data_dir: Path) -> set:
    seen_file = data_dir / "seen_ids.json"
    if seen_file.exists():
        return set(json.loads(seen_file.read_text(encoding="utf-8")))
    return set()


def save_seen(data_dir: Path, seen_ids: set):
    seen_file = data_dir / "seen_ids.json"
    seen_file.write_text(json.dumps(sorted(seen_ids)), encoding="utf-8")


# ─── 推文解析（API 响应模式） ──────────────────────────────────

def _parse_api_entry(entry):
    """从 GraphQL API 响应的 entry 解析一条推文"""
    try:
        content = entry.get("content", {})
        item = content.get("itemContent", {})
        result = item.get("tweet_results", {}).get("result", {})

        if not result or result.get("__typename") == "TweetTombstone":
            return None

        legacy = result.get("legacy", {})
        user_result = result.get("core", {}).get("user_results", {}).get("result", {})
        # X 把 screen_name 从 legacy 移到了 core
        user_core = user_result.get("core", {})
        user_legacy = user_result.get("legacy", {})
        screen_name = user_core.get("screen_name") or user_legacy.get(
            "screen_name", "unknown"
        )
        user_bio = user_legacy.get("description", "")

        text = legacy.get("full_text", "")
        if not text:
            return None

        tweet_id = result.get("rest_id", "")
        if not tweet_id:
            tweet_id = hashlib.md5(
                f"{screen_name}:{text}".encode()
            ).hexdigest()[:16]

        # 时间戳转 ISO 格式
        created_at = legacy.get("created_at", "")
        timestamp = ""
        if created_at:
            try:
                dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
                timestamp = dt.isoformat()
            except (ValueError, TypeError):
                timestamp = created_at

        # 互动数据
        likes = legacy.get("favorite_count", 0)
        retweets_count = legacy.get("retweet_count", 0)
        replies = legacy.get("reply_count", 0)
        views_raw = result.get("views", {}).get("count", "0")
        views = int(views_raw) if views_raw else 0

        # 图片
        images = []
        for m in legacy.get("extended_entities", {}).get("media", []):
            if m.get("type") == "photo":
                url = m.get("media_url_https", "")
                if url:
                    images.append(url)

        tweet_url = (
            f"https://x.com/{screen_name}/status/{tweet_id}" if tweet_id else ""
        )

        return {
            "id": tweet_id,
            "username": screen_name,
            "user_bio": user_bio,
            "text": text,
            "timestamp": timestamp,
            "url": tweet_url,
            "images": images,
            "replies": replies,
            "retweets": retweets_count,
            "likes": likes,
            "views": views,
            "collected_at": datetime.now().isoformat(),
        }
    except Exception:
        return None


def _extract_from_api_response(data):
    """从 ListLatestTweetsTimeline GraphQL 响应中提取推文和游标。

    Returns:
        (tweets, bottom_cursor): 推文列表和下一页游标
    """
    tweets = []
    bottom_cursor = ""
    try:
        instructions = (
            data.get("data", {})
            .get("list", {})
            .get("tweets_timeline", {})
            .get("timeline", {})
            .get("instructions", [])
        )
        for inst in instructions:
            for entry in inst.get("entries", []):
                entry_id = entry.get("entryId", "")
                content = entry.get("content", {})

                if content.get("cursorType") == "Bottom":
                    bottom_cursor = content.get("value", "")
                elif "tweet-" in entry_id:
                    tweet = _parse_api_entry(entry)
                    if tweet:
                        tweets.append(tweet)
    except Exception:
        pass
    return tweets, bottom_cursor


# ─── List 页面抓取（API 拦截模式） ────────────────────────────

async def fetch_list(context, list_url, settings, known_ids=None):
    """通过拦截 GraphQL API 响应抓取 List 推文。

    X 的反爬措施阻止了 headless 浏览器的 DOM 渲染，但 API 请求仍然正常返回数据。
    本函数拦截 ListLatestTweetsTimeline API 响应，直接从 JSON 中解析推文。
    支持通过 cursor 分页获取更多推文。

    Args:
        known_ids: 已持久化的推文 ID 集合。遇到大量已知推文时提前停止。
    """
    stealth = Stealth()
    page = await context.new_page()
    await stealth.apply_stealth_async(page)
    tweets = []
    timeout = settings.get("page_timeout", 30) * 1000
    max_tweets = settings.get("max_tweets", 200)
    max_pages = settings.get("max_pages", random.randint(10, 15))
    known_ids = known_ids or set()

    # 拦截 API 响应
    api_responses = []
    captured_request = {}

    async def on_response(response):
        if "ListLatestTweetsTimeline" not in response.url:
            return
        try:
            data = await response.json()
            api_responses.append(data)
            # 保存请求信息（用于后续分页请求）
            req = response.request
            if not captured_request:
                captured_request["url"] = req.url
                captured_request["headers"] = dict(req.headers)
        except Exception:
            pass

    page.on("response", on_response)

    try:
        print(f"  正在加载 List: {list_url}")
        await page.goto(list_url, wait_until="domcontentloaded", timeout=timeout)

        # 等待页面框架加载，通过滚动触发 API 请求
        # X 的反爬机制导致 API 调用时机不确定，需多次尝试
        await asyncio.sleep(5)
        for scroll_attempt in range(8):
            if api_responses:
                break
            await page.mouse.wheel(0, random.randint(600, 1000))
            await asyncio.sleep(random.uniform(2.0, 3.5))

        if not api_responses:
            # 检测是否是登录失效（跳转到登录页 / 无用户信息）
            current_url = page.url
            if "login" in current_url or "flow" in current_url:
                print("  [!] 登录已失效（被重定向到登录页）")
                return "AUTH_EXPIRED"
            print("  [!] List 页面加载超时或无内容")
            return []

        # 解析第一页
        local_seen = set()
        batch_tweets, bottom_cursor = _extract_from_api_response(
            api_responses[0]
        )

        new_count = 0
        known_count = 0
        for t in batch_tweets:
            if t["id"] in local_seen:
                continue
            local_seen.add(t["id"])
            if t["id"] in known_ids:
                known_count += 1
            else:
                tweets.append(t)
                new_count += 1

        # 分页获取更多（通过页面内 fetch 调用 GraphQL API）
        page_num = 1
        while (
            bottom_cursor
            and len(tweets) < max_tweets
            and page_num < max_pages
        ):
            page_num += 1
            # 在页面上下文中调用 fetch（自动携带 cookie 和 token）
            fetch_result = await _fetch_next_page(
                page, captured_request, bottom_cursor
            )
            if not fetch_result:
                break

            batch_tweets, bottom_cursor = _extract_from_api_response(
                fetch_result
            )
            if not batch_tweets:
                break

            new_count = 0
            for t in batch_tweets:
                if t["id"] in local_seen:
                    continue
                local_seen.add(t["id"])
                if t["id"] in known_ids:
                    known_count += 1
                else:
                    tweets.append(t)
                    new_count += 1

            # 随机延迟，模拟浏览行为
            await asyncio.sleep(random.uniform(1.5, 3.0))

        if known_count > 0:
            print(
                f"  已知推文 {known_count} 条，"
                f"新推文 {len(tweets)} 条（共 {page_num} 页）"
            )
        else:
            print(f"  累计解析 {len(tweets)} 条新推文（共 {page_num} 页）")

    except Exception as e:
        print(f"  [!] 抓取失败: {e}")
    finally:
        await page.close()

    return tweets


async def _fetch_next_page(page, captured_request, cursor):
    """通过页面内 fetch 请求下一页数据。"""
    try:
        original_url = captured_request.get("url", "")
        headers = captured_request.get("headers", {})
        if not original_url:
            return None

        # 解析原始 URL，替换 variables 中的 cursor
        parsed = urllib.parse.urlparse(original_url)
        params = urllib.parse.parse_qs(parsed.query)

        variables = json.loads(params["variables"][0])
        variables["cursor"] = cursor
        params["variables"] = [json.dumps(variables)]

        new_query = urllib.parse.urlencode(
            {k: v[0] for k, v in params.items()}
        )
        new_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{new_query}"

        # 构建 headers JSON（只保留必要的）
        fetch_headers = {}
        for h in [
            "authorization",
            "x-csrf-token",
            "x-twitter-auth-type",
            "x-twitter-active-user",
            "x-twitter-client-language",
            "content-type",
        ]:
            if h in headers:
                fetch_headers[h] = headers[h]

        # 在页面上下文中执行 fetch
        result = await page.evaluate(
            """async ([url, hdrs]) => {
                try {
                    const resp = await fetch(url, {
                        method: 'GET',
                        headers: hdrs,
                        credentials: 'include'
                    });
                    if (!resp.ok) return null;
                    return await resp.json();
                } catch(e) {
                    return null;
                }
            }""",
            [new_url, fetch_headers],
        )
        return result
    except Exception:
        return None


# ─── 数据存储 ─────────────────────────────────────────────────

def save_tweets_json(data_dir: Path, tweets: list):
    """追加保存推文到 JSON 文件"""
    tweets_file = data_dir / "tweets.json"
    existing = []
    if tweets_file.exists():
        try:
            existing = json.loads(tweets_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = []

    existing.extend(tweets)
    tweets_file.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def generate_report(data_dir: Path, tweets: list):
    """生成每日 Markdown 报告"""
    reports_dir = data_dir / "reports"
    reports_dir.mkdir(exist_ok=True)

    today = date.today().isoformat()
    report_file = reports_dir / f"{today}.md"

    # 按账号分组
    by_account = {}
    for t in tweets:
        by_account.setdefault(t["username"], []).append(t)

    lines = [f"# X 监控日报 - {today}\n"]
    lines.append(
        f"共监控到 **{len(tweets)}** 条新推文，"
        f"来自 **{len(by_account)}** 个账号\n"
    )

    for username in sorted(by_account.keys()):
        account_tweets = by_account[username]
        lines.append(f"\n## @{username} ({len(account_tweets)} 条)\n")

        for t in account_tweets:
            ts = t.get("timestamp", "")
            if ts:
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    ts = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass

            lines.append(f"### {ts}\n")
            lines.append(f"{t['text']}\n")
            if t.get("url"):
                lines.append(f"[原文链接]({t['url']})\n")
            if t.get("images"):
                for img in t["images"]:
                    lines.append(f"![图片]({img})\n")
            lines.append("---\n")

    mode = "a" if report_file.exists() else "w"
    with open(report_file, mode, encoding="utf-8") as f:
        if mode == "a":
            f.write(
                f"\n\n---\n\n# 更新于 "
                f"{datetime.now().strftime('%H:%M:%S')}\n\n"
            )
        f.write("\n".join(lines))

    print(f"  报告已保存: {report_file}")


# ─── 登录 ─────────────────────────────────────────────────────

def login_with_cookies(auth_token, ct0):
    """通过手动提供的 cookies 生成会话文件"""
    auth_file = BASE_DIR / "auth_state.json"

    cookies = [
        {
            "name": "auth_token",
            "value": auth_token,
            "domain": ".x.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "None",
        },
        {
            "name": "ct0",
            "value": ct0,
            "domain": ".x.com",
            "path": "/",
            "httpOnly": False,
            "secure": True,
            "sameSite": "Lax",
        },
    ]

    state = {"cookies": cookies, "origins": []}
    auth_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"会话已保存到 {auth_file}")
    print("现在可以运行 python3 monitor.py 开始监控了。")


# ─── 主监控逻辑 ─────────────────────────────────────────────

async def run_once(config, quiet=False):
    """执行一次监控，返回新推文列表"""
    settings = config["settings"]
    list_urls = config["lists"]
    data_dir = BASE_DIR / settings.get("output_dir", "./data")
    data_dir.mkdir(parents=True, exist_ok=True)
    auth_file = BASE_DIR / "auth_state.json"

    log = (lambda *a, **k: None) if quiet else print

    if not auth_file.exists():
        print("[!] 未找到登录会话，请先运行:", file=sys.stderr)
        print("    python3 monitor.py --login --auth-token <值> --ct0 <值>",
              file=sys.stderr)
        sys.exit(1)

    seen_ids = load_seen(data_dir)

    log(f"开始监控 {len(list_urls)} 个 List...")
    log(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("-" * 50)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=settings.get("headless", True),
            channel="chrome",
        )
        context = await browser.new_context(
            storage_state=str(auth_file),
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )

        all_new_tweets = []
        auth_expired = False
        for i, list_url in enumerate(list_urls):
            tweets = await fetch_list(context, list_url, settings, known_ids=seen_ids)

            # 检测 auth 失效
            if tweets == "AUTH_EXPIRED":
                auth_expired = True
                break

            new_tweets = [t for t in tweets if t["id"] not in seen_ids]

            if new_tweets:
                usernames = set(t["username"] for t in new_tweets)
                log(
                    f"  -> {len(new_tweets)} 条新推文，"
                    f"来自 {len(usernames)} 个账号"
                )
                all_new_tweets.extend(new_tweets)
                for t in new_tweets:
                    seen_ids.add(t["id"])
            else:
                log(f"  -> 无新推文 (共 {len(tweets)} 条已知)")

            # 多个 List 之间随机延迟
            if i < len(list_urls) - 1:
                delay = random.uniform(
                    settings.get("delay_min", 5),
                    settings.get("delay_max", 15),
                )
                await asyncio.sleep(delay)

        await browser.close()

    # auth 失效：不保存，返回特殊标记
    if auth_expired:
        log("[!] Cookie 已过期，需要手动更新")
        return "AUTH_EXPIRED"

    # 保存结果
    if all_new_tweets:
        save_tweets_json(data_dir, all_new_tweets)
        save_seen(data_dir, seen_ids)

        if settings.get("generate_report", True):
            generate_report(data_dir, all_new_tweets)

        log(f"\n{'=' * 50}")
        log(f"本次共发现 {len(all_new_tweets)} 条新推文")
        log(f"数据已保存到 {data_dir}/tweets.json")
    else:
        save_seen(data_dir, seen_ids)
        log(f"\n{'=' * 50}")
        log("本次无新推文")

    return all_new_tweets


async def run_loop(config, interval=300):
    """循环监控模式"""
    print(f"进入循环监控模式，每 {interval} 秒检查一次")
    print("按 Ctrl+C 退出\n")

    while True:
        try:
            await run_once(config)
            print(f"\n下次检查: {interval} 秒后...")
            await asyncio.sleep(interval)
        except KeyboardInterrupt:
            print("\n监控已停止")
            break
        except Exception as e:
            print(f"\n[!] 运行出错: {e}")
            print(f"将在 {interval} 秒后重试...")
            await asyncio.sleep(interval)


# ─── Pipeline 过滤 ────────────────────────────────────────────

_URL_RE = re.compile(r"https?://\S+")


def _is_low_value(text: str) -> bool:
    """低价值推文：纯转推、纯链接。"""
    stripped = text.strip()
    if stripped.startswith("RT @"):
        return True
    without_urls = _URL_RE.sub("", stripped).strip()
    return len(without_urls) < 10


def _has_enough_engagement(tweet: dict) -> bool:
    """互动量是否达标。新推文(<3h)阈值更低，无数据则保留。"""
    likes = tweet.get("likes")
    views = tweet.get("views")
    if likes is None and views is None:
        return True
    ts = tweet.get("timestamp", "")
    is_fresh = False
    if ts:
        try:
            pub_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600
            is_fresh = age_hours < 3
        except (ValueError, TypeError):
            pass
    if is_fresh:
        return (likes or 0) >= 1 or (views or 0) >= 100
    return (likes or 0) >= 3 or (views or 0) >= 500


def _filter_by_relevance(tweets: list) -> list:
    """调用 DeepSeek 判断推文是否与 AI/科技相关。无 key 则跳过。"""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("  [Filter] DEEPSEEK_API_KEY not set, skipping relevance filter")
        return tweets

    if not tweets:
        return tweets

    items = []
    for i, t in enumerate(tweets):
        text = t["text"][:200]
        items.append(f"[{i}] @{t['username']}: {text}")

    prompt = (
        f"以下是从 Twitter 抓取的 {len(tweets)} 条推文。\n"
        "请判断每条是否与 AI、机器学习、大模型、科技行业、开发者工具 相关。\n"
        "仅返回相关推文的序号列表（JSON 数组），如 [0, 2, 5, 8]。\n"
        "不相关的包括：个人生活、政治、体育、纯营销、无实质内容的闲聊等。\n"
        "宁可多保留，不要误删。如果不确定，算作相关。\n\n"
        "推文列表：\n"
        + "\n".join(items)
        + "\n\n返回 JSON 数组（仅序号）："
    )

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        resp = client.chat.completions.create(
            model="deepseek-chat",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.choices[0].message.content.strip()
        match = re.search(r'\[[\d,\s]*\]', text)
        if match:
            indices = set(json.loads(match.group()))
            filtered = [t for i, t in enumerate(tweets) if i in indices]
            return filtered
        else:
            print("  [Filter] Failed to parse relevance response, keeping all")
            return tweets
    except Exception as e:
        print(f"  [Filter] DeepSeek error: {e}, keeping all")
        return tweets


async def run_pipeline(config, output_path=None):
    """流水线模式：静默运行，输出结构化 JSON 供下游消费。
    多次运行同一天时，自动合并新推文到已有文件。
    退出码: 0=成功, 1=一般错误, 2=Cookie过期需手动更新"""
    tweets = await run_once(config, quiet=True)

    # auth 失效，返回特殊退出码
    if tweets == "AUTH_EXPIRED":
        sys.exit(2)

    # 确定输出路径
    if output_path:
        out = Path(output_path)
    else:
        data_dir = BASE_DIR / config["settings"].get("output_dir", "./data")
        pipeline_dir = data_dir / "pipeline"
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        out = pipeline_dir / f"{date.today().isoformat()}.json"

    out.parent.mkdir(parents=True, exist_ok=True)

    # 合并模式：如果今天的文件已存在，把新推文追加进去
    existing_tweets = []
    existing_ids = set()
    if out.exists():
        try:
            existing_data = json.loads(out.read_text(encoding="utf-8"))
            existing_tweets = existing_data.get("tweets", [])
            existing_ids = {t["id"] for t in existing_tweets}
        except (json.JSONDecodeError, KeyError):
            pass

    # 去重：先收集本轮新推文
    new_tweets = [t for t in tweets if t["id"] not in existing_ids]

    # 硬规则过滤（质量 + 互动）
    before_hard = len(new_tweets)
    new_tweets = [t for t in new_tweets if not _is_low_value(t.get("text", ""))]
    new_tweets = [t for t in new_tweets if _has_enough_engagement(t)]
    after_hard = len(new_tweets)
    if before_hard > 0:
        print(f"  硬规则过滤: {before_hard} → {after_hard}")

    # DeepSeek 语义过滤（AI/科技相关性）
    if new_tweets:
        before_ai = len(new_tweets)
        new_tweets = _filter_by_relevance(new_tweets)
        print(f"  语义过滤: {before_ai} → {len(new_tweets)}")

    # 合并到已有数据（保留 engagement 字段）
    merged_tweets = list(existing_tweets)
    new_count = 0
    for t in new_tweets:
        merged_tweets.append({
            "id": t["id"],
            "username": t["username"],
            "text": t["text"],
            "timestamp": t["timestamp"],
            "url": t["url"],
            "images": t["images"],
            "likes": t.get("likes", 0),
            "retweets": t.get("retweets", 0),
            "views": t.get("views", 0),
        })
        existing_ids.add(t["id"])
        new_count += 1

    result = {
        "source": "x-monitor",
        "scraped_at": datetime.now().isoformat(),
        "date": date.today().isoformat(),
        "list_urls": config["lists"],
        "tweet_count": len(merged_tweets),
        "account_count": len(set(t["username"] for t in merged_tweets)) if merged_tweets else 0,
        "tweets": merged_tweets,
    }

    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # stdout 输出摘要（不输出全量 JSON，避免多次运行后输出过大）
    summary = {
        "date": result["date"],
        "new_tweets": new_count,
        "total_tweets": result["tweet_count"],
        "total_accounts": result["account_count"],
    }
    print(json.dumps(summary, ensure_ascii=False))

    return tweets


# ─── 入口 ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="X (Twitter) List 监控工具")
    parser.add_argument(
        "--login", action="store_true",
        help="通过 cookies 设置登录会话",
    )
    parser.add_argument(
        "--auth-token", type=str, help="X 的 auth_token cookie 值"
    )
    parser.add_argument(
        "--ct0", type=str, help="X 的 ct0 cookie 值"
    )
    parser.add_argument(
        "--loop", action="store_true", help="循环监控模式"
    )
    parser.add_argument(
        "--interval", type=int, default=300,
        help="循环间隔（秒），默认 300",
    )
    parser.add_argument(
        "--pipeline", action="store_true",
        help="流水线模式：静默运行，输出结构化 JSON 到 stdout 和文件",
    )
    parser.add_argument(
        "--pipeline-output", type=str, default=None,
        help="流水线模式的 JSON 输出路径（默认 data/pipeline/{日期}.json）",
    )
    parser.add_argument(
        "--config", type=str, default="config.yaml",
        help="配置文件路径",
    )
    args = parser.parse_args()

    if args.login:
        if not args.auth_token or not args.ct0:
            print(
                "用法: python3 monitor.py --login "
                "--auth-token <值> --ct0 <值>"
            )
            print()
            print("获取方法:")
            print("  1. 用浏览器打开 https://x.com 并登录")
            print("  2. 右键页面 → 检查 → 存储/Application → Cookies")
            print("  3. 复制 auth_token 和 ct0 的值")
            sys.exit(1)
        login_with_cookies(args.auth_token, args.ct0)
    else:
        config = load_config(args.config)
        try:
            if args.pipeline:
                tweets = asyncio.run(run_pipeline(config, args.pipeline_output))
                sys.exit(0 if tweets is not None else 1)
            elif args.loop:
                asyncio.run(run_loop(config, args.interval))
            else:
                asyncio.run(run_once(config))
        except Exception as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
