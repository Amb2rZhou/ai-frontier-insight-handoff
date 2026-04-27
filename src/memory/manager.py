"""Memory manager for persistent trend tracking and signal accumulation.

Manages three JSON files in the memory/ directory:
- weekly_signals.json: Accumulated daily signals for the current week
- trends.json: Long-term trend tracking with trajectories
- history_insights.json: Historical predictions for self-correction
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from ..utils.config import MEMORY_DIR, get_timezone


def _load_json(filename: str) -> dict:
    """Load a JSON file from the memory directory."""
    path = os.path.join(MEMORY_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_json(filename: str, data: dict):
    """Save a dict as JSON to the memory directory."""
    os.makedirs(MEMORY_DIR, exist_ok=True)
    path = os.path.join(MEMORY_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────
# Weekly Signals
# ──────────────────────────────────────────────

def load_weekly_signals() -> dict:
    """Load the current week's accumulated signals."""
    return _load_json("weekly_signals.json") or {
        "current_week": None,
        "days": {},
        "archived_weeks": [],
    }


def save_daily_signals(date: str, signals: List[Dict]):
    """Append today's signals to the weekly accumulator.

    Args:
        date: Date string (YYYY-MM-DD)
        signals: List of signal dicts from signal_extractor
    """
    data = load_weekly_signals()

    tz = ZoneInfo(get_timezone())
    now = datetime.now(tz)
    current_week = now.strftime("%Y-W%W")

    # If new week started, archive the old one
    if data.get("current_week") and data["current_week"] != current_week:
        archive = {
            "week": data["current_week"],
            "days": data.get("days", {}),
        }
        if "archived_weeks" not in data:
            data["archived_weeks"] = []
        data["archived_weeks"].append(archive)
        data["days"] = {}
        print(f"  Memory: Archived week {data['current_week']}, starting {current_week}")

    data["current_week"] = current_week
    data["days"][date] = signals

    _save_json("weekly_signals.json", data)
    print(f"  Memory: Saved {len(signals)} signals for {date} (week {current_week})")


def get_week_signals() -> List[Dict]:
    """Get all signals from the current week as a flat list."""
    data = load_weekly_signals()
    all_signals = []
    for day_signals in data.get("days", {}).values():
        all_signals.extend(day_signals)
    return all_signals


def get_recent_signal_titles(days: int = 3, exclude_date: str = None) -> List[str]:
    """Get signal titles from recent days for dedup context.

    Args:
        days: How many days back to look.
        exclude_date: Date to exclude (typically today, to avoid self-dedup).

    Returns:
        List of signal title strings.
    """
    from datetime import datetime, timedelta

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    data = load_weekly_signals()
    titles = []

    # Collect all day→signals pairs from current week + last archived week
    all_days = dict(data.get("days", {}))
    for archive in data.get("archived_weeks", [])[-1:]:
        for day, signals in archive.get("days", {}).items():
            if day not in all_days:
                all_days[day] = signals

    for day, signals in all_days.items():
        if day == exclude_date or day < cutoff:
            continue
        titles.extend(s.get("title", "") for s in signals if s.get("title"))

    return titles


def rotate_weekly_signals():
    """Archive current week and reset for new week.

    Called after weekly report generation.
    """
    data = load_weekly_signals()
    if data.get("days"):
        archive = {
            "week": data.get("current_week"),
            "days": data.get("days", {}),
        }
        if "archived_weeks" not in data:
            data["archived_weeks"] = []
        data["archived_weeks"].append(archive)

    # Keep only last 12 weeks of archives
    max_archives = 12
    if len(data.get("archived_weeks", [])) > max_archives:
        data["archived_weeks"] = data["archived_weeks"][-max_archives:]

    data["current_week"] = None
    data["days"] = {}
    _save_json("weekly_signals.json", data)
    print("  Memory: Weekly signals rotated and archived")


# ──────────────────────────────────────────────
# Trends
# ──────────────────────────────────────────────

def load_trends() -> dict:
    """Load the trend tracking database."""
    return _load_json("trends.json") or {
        "last_updated": None,
        "trends": [],
    }


def save_trends(trends_data: dict):
    """Save updated trend data."""
    tz = ZoneInfo(get_timezone())
    trends_data["last_updated"] = datetime.now(tz).isoformat()
    _save_json("trends.json", trends_data)
    count = len(trends_data.get("trends", []))
    print(f"  Memory: Saved {count} trends")


def update_trends_from_ai(ai_result: dict):
    """Apply AI-generated trend updates to the trend database.

    Args:
        ai_result: Parsed JSON from trend_update prompt containing
                   'updated_trends' and 'new_trends'
    """
    data = load_trends()
    trends = data.get("trends", [])
    trends_by_id = {t.get("id", t.get("name", "")): t for t in trends}

    tz = ZoneInfo(get_timezone())
    today = datetime.now(tz).strftime("%Y-%m-%d")

    # Apply updates to existing trends
    for update in ai_result.get("updated_trends", []):
        tid = update.get("id", "")
        if tid in trends_by_id:
            trend = trends_by_id[tid]
            trend["trajectory"] = update.get("trajectory", trend.get("trajectory"))
            delta = update.get("signal_count_delta", 0)
            trend["signal_count"] = trend.get("signal_count", 0) + delta

            # Track weekly counts
            if "weekly_counts" not in trend:
                trend["weekly_counts"] = []
            # Append delta to current week (simplified)
            trend["weekly_counts"].append(delta)
            if len(trend["weekly_counts"]) > 12:
                trend["weekly_counts"] = trend["weekly_counts"][-12:]

            # Add key event if significant
            event = update.get("new_key_event")
            if event:
                if "key_events" not in trend:
                    trend["key_events"] = []
                trend["key_events"].append({"date": today, "event": event})

    # Add new trends
    for new_trend in ai_result.get("new_trends", []):
        trend_id = new_trend["name"].lower().replace(" ", "_")[:40]
        if trend_id not in trends_by_id:
            trends.append({
                "id": trend_id,
                "name": new_trend["name"],
                "related_tags": new_trend.get("related_tags", []),
                "trajectory": new_trend.get("initial_trajectory", "stable"),
                "signal_count": 1,
                "weekly_counts": [1],
                "key_events": [{"date": today, "event": "First detected"}],
                "created": today,
            })

    # Merge trends if suggested by AI
    for merge in ai_result.get("merge_trends", []):
        source_id = merge.get("source_id", "")
        target_id = merge.get("target_id", "")
        if source_id in trends_by_id and target_id in trends_by_id:
            source = trends_by_id[source_id]
            target = trends_by_id[target_id]
            # Transfer signal count
            target["signal_count"] = target.get("signal_count", 0) + source.get("signal_count", 0)
            # Merge tags
            existing_tags = set(target.get("related_tags", []))
            existing_tags.update(source.get("related_tags", []))
            target["related_tags"] = list(existing_tags)[:8]
            # Merge key events
            target_events = target.get("key_events", [])
            target_events.extend(source.get("key_events", []))
            target["key_events"] = sorted(target_events, key=lambda e: e.get("date", ""))[-10:]
            # Remove source trend
            trends = [t for t in trends if t.get("id") != source_id]
            print(f"  Trend merged: '{source.get('name')}' → '{target.get('name')}' ({merge.get('reason', '')})")

    data["trends"] = trends
    save_trends(data)


# ──────────────────────────────────────────────
# History Insights (predictions for self-correction)
# ──────────────────────────────────────────────

def load_history_insights() -> dict:
    """Load historical predictions."""
    return _load_json("history_insights.json") or {
        "predictions": [],
    }


def save_prediction(prediction: Dict):
    """Save a new prediction for future self-correction.

    Args:
        prediction: Dict with keys: date, prediction_text, category, timeframe
    """
    data = load_history_insights()
    data["predictions"].append(prediction)

    # Keep last 100 predictions
    if len(data["predictions"]) > 100:
        data["predictions"] = data["predictions"][-100:]

    _save_json("history_insights.json", data)


def get_recent_predictions(weeks: int = 4) -> List[Dict]:
    """Get predictions from the last N weeks for self-correction context."""
    data = load_history_insights()
    predictions = data.get("predictions", [])

    tz = ZoneInfo(get_timezone())
    now = datetime.now(tz)

    recent = []
    for p in predictions:
        try:
            p_date = datetime.fromisoformat(p["date"])
            if hasattr(p_date, "tzinfo") and p_date.tzinfo is None:
                p_date = p_date.replace(tzinfo=tz)
            days_ago = (now - p_date).days
            if days_ago <= weeks * 7:
                recent.append(p)
        except (KeyError, ValueError):
            continue

    return recent
