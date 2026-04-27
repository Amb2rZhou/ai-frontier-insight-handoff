"""Draft save/load/lifecycle management.

Drafts are JSON files in config/drafts/ with status lifecycle:
  pending_review → approved → sent
"""

import json
import os
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from .config import DRAFTS_DIR, get_timezone


def save_draft(data: dict, draft_type: str = "daily") -> str:
    """Save analysis results as a draft JSON file.

    Args:
        data: The analysis data dict (must contain 'date' or 'week')
        draft_type: "daily" or "weekly"

    Returns:
        Draft file path
    """
    os.makedirs(DRAFTS_DIR, exist_ok=True)

    if draft_type == "weekly":
        week = data.get("week", datetime.now().strftime("%Y-W%W"))
        filename = f"{week}_weekly.json"
    else:
        date = data.get("date", datetime.now().strftime("%Y-%m-%d"))
        filename = f"{date}_daily.json"

    draft_path = os.path.join(DRAFTS_DIR, filename)

    # Never overwrite a draft that's already been sent
    if os.path.exists(draft_path):
        try:
            with open(draft_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if existing.get("status") in ("sent", "approved"):
                print(f"  - Skipping {filename}: already {existing['status']}")
                return draft_path
        except (json.JSONDecodeError, IOError):
            pass  # Corrupted file, safe to overwrite

    tz = ZoneInfo(get_timezone())
    draft_data = {
        **data,
        "type": draft_type,
        "status": data.get("status", "pending_review"),
        "created_at": datetime.now(tz).isoformat(),
    }

    with open(draft_path, "w", encoding="utf-8") as f:
        json.dump(draft_data, f, ensure_ascii=False, indent=2)

    print(f"  - Draft saved: {filename}")
    cleanup_old_drafts()
    return draft_path


def load_draft(date: str = None, draft_type: str = "daily") -> Optional[dict]:
    """Load a draft by date/week.

    Args:
        date: Date string (YYYY-MM-DD for daily, YYYY-WNN for weekly).
              Defaults to today.
        draft_type: "daily" or "weekly"

    Returns:
        Draft data dict or None
    """
    if date is None:
        tz = ZoneInfo(get_timezone())
        date = datetime.now(tz).strftime("%Y-%m-%d")

    if draft_type == "weekly":
        filename = f"{date}_weekly.json"
    else:
        filename = f"{date}_daily.json"

    draft_path = os.path.join(DRAFTS_DIR, filename)

    try:
        with open(draft_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def update_draft_status(date: str, status: str, draft_type: str = "daily") -> bool:
    """Update the status of an existing draft.

    Returns True if updated successfully.
    """
    draft = load_draft(date, draft_type)
    if draft is None:
        return False

    draft["status"] = status
    tz = ZoneInfo(get_timezone())
    draft["updated_at"] = datetime.now(tz).isoformat()

    if draft_type == "weekly":
        filename = f"{date}_weekly.json"
    else:
        filename = f"{date}_daily.json"

    draft_path = os.path.join(DRAFTS_DIR, filename)
    with open(draft_path, "w", encoding="utf-8") as f:
        json.dump(draft, f, ensure_ascii=False, indent=2)

    return True


def cleanup_old_drafts(days: int = 30):
    """Delete draft files older than specified days."""
    if not os.path.isdir(DRAFTS_DIR):
        return

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    deleted = []

    for filename in os.listdir(DRAFTS_DIR):
        if not filename.endswith(".json"):
            continue
        # Date is always the first 10 chars (YYYY-MM-DD) or 8 chars (YYYY-WNN)
        file_date = filename[:10]
        if len(file_date) == 10 and file_date < cutoff:
            os.remove(os.path.join(DRAFTS_DIR, filename))
            deleted.append(filename)

    if deleted:
        print(f"  - Cleaned up {len(deleted)} old drafts")
