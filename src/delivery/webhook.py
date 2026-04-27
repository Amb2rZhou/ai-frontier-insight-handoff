"""RedCity webhook delivery — supports multiple named channels.

Channel config via WEBHOOK_CHANNELS env var (JSON):
  {"测试": "key1", "夏月": "key2"}

Channels are tagged with roles in settings.yaml:
  delivery.webhook.alert_channels: ["测试"]
  (defaults to first channel only)
"""

import json
import os
import urllib.error
import urllib.request
from typing import List, Optional

from ..utils.config import load_settings


# ── Channel resolution ─────────────────────────────────────────

def _get_channels() -> dict:
    """Get all named webhook channels.

    Returns:
        dict of {name: key}
    """
    # Primary: WEBHOOK_CHANNELS JSON  {"name": "key", ...}
    raw = os.environ.get("WEBHOOK_CHANNELS", "").strip()
    if raw:
        try:
            channels = json.loads(raw)
            if isinstance(channels, dict) and channels:
                return {k: v.strip() for k, v in channels.items() if v.strip()}
        except (json.JSONDecodeError, AttributeError):
            pass

    # Fallback: legacy WEBHOOK_KEY (single or comma-separated)
    legacy = os.environ.get("WEBHOOK_KEY", "").strip()
    if legacy:
        keys = [k.strip() for k in legacy.split(",") if k.strip()]
        if len(keys) == 1:
            return {"default": keys[0]}
        return {f"channel_{i+1}": k for i, k in enumerate(keys)}

    print("  Warning: No webhook channels found (set WEBHOOK_CHANNELS env var)")
    return {}


# ── Low-level send ─────────────────────────────────────────────

def _post_webhook(url: str, content: str, mention_all: bool = True) -> str:
    """Post a single markdown message to webhook.

    Returns:
        "ok"            - success
        "api_error"     - server rejected (safe to retry smaller)
        "network_error" - network issue (NOT safe to retry)
    """
    markdown_body = {"content": content}
    if mention_all:
        markdown_body["mentioned_list"] = ["@all"]
    payload = {
        "msgtype": "markdown",
        "markdown": markdown_body,
    }

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            print(f"  Webhook response: {result}")
            errcode = result.get("errcode", 0)
            if errcode != 0:
                errmsg = result.get("errmsg", "unknown error")
                print(f"  Webhook API error: {errcode} - {errmsg}")
                return "api_error"
            return "ok"
    except urllib.error.HTTPError as e:
        print(f"  Webhook HTTP error: {e.code} {e.reason}")
        return "api_error"
    except Exception as e:
        print(f"  Webhook network error: {e}")
        return "network_error"


def _send_one(url: str, content: str, mention_all: bool) -> bool:
    """Send to a single webhook URL with retry on API error."""
    result = _post_webhook(url, content, mention_all=mention_all)
    if result == "ok":
        return True

    if result == "network_error":
        print("  Network error — skipping retry to avoid duplicate")
        return False

    # API error — try trimming content
    for ratio in [0.8, 0.6, 0.4]:
        truncated = content[:int(len(content) * ratio)]
        last_newline = truncated.rfind("\n")
        if last_newline > len(truncated) * 0.5:
            truncated = truncated[:last_newline]
        truncated += "\n\n---\n(message truncated)"

        print(f"  Retrying at {int(ratio*100)}% ({len(truncated.encode('utf-8'))} bytes)")
        result = _post_webhook(url, truncated, mention_all=mention_all)
        if result == "ok":
            return True
        if result == "network_error":
            print("  Network error during retry — stopping")
            return False

    print("  All retry attempts failed")
    return False


# ── Public API ─────────────────────────────────────────────────

def send_webhook(content: str, mention_all: bool = True,
                 alert_only: bool = False) -> bool:
    """Send markdown content to RedCity webhook channels.

    Args:
        content: Markdown string to send
        mention_all: Whether to @all in this message
        alert_only: If True, only send to alert channels (e.g. 测试)

    Returns:
        True if at least one channel succeeded
    """
    channels = _get_channels()
    if not channels:
        return False

    settings = load_settings()
    webhook_cfg = settings.get("delivery", {}).get("webhook", {})
    url_base = webhook_cfg.get(
        "url_base",
        "https://redcity-open.xiaohongshu.com/api/robot/webhook/send",
    )

    # Filter to alert-only channels if requested
    if alert_only:
        alert_names = webhook_cfg.get("alert_channels", [])
        if not alert_names:
            # Default: first channel only
            first_name = next(iter(channels))
            alert_names = [first_name]
        channels = {k: v for k, v in channels.items() if k in alert_names}
        if not channels:
            print("  Warning: No alert channels matched")
            return False

    content_bytes = len(content.encode("utf-8"))
    names = ", ".join(channels.keys())
    print(f"  Webhook message: {content_bytes} bytes → [{names}]")

    any_ok = False
    for i, (name, key) in enumerate(channels.items()):
        url = f"{url_base}?key={key}"
        tag = f"[{i+1}/{len(channels)} {name}]"
        print(f"  {tag} Sending...")
        if _send_one(url, content, mention_all):
            print(f"  {tag} OK")
            any_ok = True
        else:
            print(f"  {tag} FAILED")

    return any_ok
