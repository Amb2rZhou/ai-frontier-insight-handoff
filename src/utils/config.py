"""Configuration loader for AI Frontier Insight Bot."""

import os
import yaml

# Project root: two levels up from this file (src/utils/config.py → project root)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")
MEMORY_DIR = os.path.join(PROJECT_ROOT, "memory")
PROMPTS_DIR = os.path.join(PROJECT_ROOT, "prompts")
DRAFTS_DIR = os.path.join(CONFIG_DIR, "drafts")


def load_settings() -> dict:
    """Load settings.yaml and return as dict."""
    path = os.path.join(CONFIG_DIR, "settings.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_sources() -> dict:
    """Load sources.yaml and return as dict."""
    path = os.path.join(CONFIG_DIR, "sources.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_prompt(name: str, **kwargs) -> str:
    """Load a prompt template and format with kwargs.

    Args:
        name: Prompt file name without extension (e.g. "signal_extraction")
        **kwargs: Template variables to substitute

    Returns:
        Formatted prompt string
    """
    path = os.path.join(PROMPTS_DIR, f"{name}.txt")
    with open(path, "r", encoding="utf-8") as f:
        template = f.read()
    if kwargs:
        return template.format(**kwargs)
    return template


def get_timezone() -> str:
    """Return configured timezone string."""
    settings = load_settings()
    return settings.get("timezone", "Asia/Shanghai")


def get_schedule() -> dict:
    """Return computed schedule times derived from send_hour.

    Returns dict with keys: send_hour, send_minute, fetch_hour, fetch_minute,
                            xmonitor_hour, xmonitor_minute
    """
    settings = load_settings()
    daily = settings.get("schedule", {}).get("daily", {})

    send_hour = daily.get("send_hour", 8)
    send_minute = daily.get("send_minute", 0)
    fetch_offset = daily.get("fetch_offset_minutes", 30)

    # Compute fetch time = send_time - fetch_offset
    total_send_min = send_hour * 60 + send_minute
    total_fetch_min = total_send_min - fetch_offset
    if total_fetch_min < 0:
        total_fetch_min += 24 * 60

    # x-monitor = send_time - 45 min
    total_xmon_min = total_send_min - 45
    if total_xmon_min < 0:
        total_xmon_min += 24 * 60

    return {
        "send_hour": send_hour,
        "send_minute": send_minute,
        "fetch_hour": total_fetch_min // 60,
        "fetch_minute": total_fetch_min % 60,
        "xmonitor_hour": total_xmon_min // 60,
        "xmonitor_minute": total_xmon_min % 60,
    }
