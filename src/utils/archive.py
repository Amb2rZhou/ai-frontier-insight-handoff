"""Daily archive and data cleanup utilities.

Archives daily pipeline outputs (brief + referenced sources) for weekly report generation.
Cleans up old source files and x-monitor data to prevent unbounded growth.
"""

import json
import os
from datetime import datetime, timedelta

from .config import PROJECT_ROOT

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")
X_MONITOR_DIR = os.path.join(DATA_DIR, "x-monitor")


def archive_daily(date_str: str, raw_items: list, insights: list, trend_summary: str):
    """Save daily archive to data/daily/{date}/.

    brief.json: signals, insights, trend summary (permanent record).
    sources.json: raw items referenced by signals via raw_item_indices.

    Args:
        date_str: Date string YYYY-MM-DD.
        raw_items: Full list of RawItem objects from collectors.
        insights: List of insight dicts (with raw_item_indices).
        trend_summary: Trend summary text.
    """
    day_dir = os.path.join(DAILY_DIR, date_str)
    os.makedirs(day_dir, exist_ok=True)

    # Collect referenced raw item indices from all insights
    referenced_indices = set()
    for insight in insights:
        for idx in insight.get("raw_item_indices", []):
            if isinstance(idx, int) and 0 <= idx < len(raw_items):
                referenced_indices.add(idx)

    # Build sources list from referenced raw items
    source_items = []
    for idx in sorted(referenced_indices):
        item = raw_items[idx]
        item_dict = item.to_dict() if hasattr(item, "to_dict") else dict(item)
        item_dict["index"] = idx
        source_items.append(item_dict)

    # brief.json
    brief = {
        "date": date_str,
        "signal_count": len(insights),
        "raw_item_count": len(raw_items),
        "insights": [
            {
                "title": i.get("title", ""),
                "signal_text": i.get("signal_text", ""),
                "signal_strength": i.get("signal_strength", 0),
                "insight": i.get("insight", ""),
                "implication": i.get("implication", ""),
                "category": i.get("category", ""),
                "sources": i.get("sources", []),
                "tags": i.get("tags", []),
            }
            for i in insights
        ],
        "trend_summary": trend_summary or "",
    }

    brief_path = os.path.join(day_dir, "brief.json")
    with open(brief_path, "w", encoding="utf-8") as f:
        json.dump(brief, f, ensure_ascii=False, indent=2)

    # sources.json
    sources = {
        "date": date_str,
        "item_count": len(source_items),
        "items": source_items,
    }

    sources_path = os.path.join(day_dir, "sources.json")
    with open(sources_path, "w", encoding="utf-8") as f:
        json.dump(sources, f, ensure_ascii=False, indent=2)

    print(f"  - Archived: {len(brief['insights'])} insights, {len(source_items)} source items → {day_dir}")


def cleanup_old_data(daily_sources_days: int = 30, x_monitor_days: int = 14):
    """Clean up expired data files.

    - data/daily/{date}/sources.json: deleted after daily_sources_days (brief.json kept forever).
    - data/x-monitor/{date}.json: deleted after x_monitor_days.

    Args:
        daily_sources_days: Days to keep sources.json files.
        x_monitor_days: Days to keep x-monitor raw files.
    """
    today = datetime.now()
    deleted_count = 0

    # Clean daily sources.json
    if os.path.isdir(DAILY_DIR):
        sources_cutoff = (today - timedelta(days=daily_sources_days)).strftime("%Y-%m-%d")
        for entry in os.listdir(DAILY_DIR):
            entry_path = os.path.join(DAILY_DIR, entry)
            if not os.path.isdir(entry_path):
                continue
            # Directory name is YYYY-MM-DD
            if len(entry) == 10 and entry < sources_cutoff:
                sources_path = os.path.join(entry_path, "sources.json")
                if os.path.exists(sources_path):
                    os.remove(sources_path)
                    deleted_count += 1

    # x-monitor data: keep indefinitely (was 14-day auto-delete, disabled 2026-04-27)

    if deleted_count:
        print(f"  - Cleanup: removed {deleted_count} expired files")
