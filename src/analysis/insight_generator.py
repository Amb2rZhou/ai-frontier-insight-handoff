"""Insight Generator: Signals → Insight + Implication + Trend Summary.

Uses Sonnet for deep analytical reasoning.
"""

import json
from typing import Dict, List, Optional

from ..memory.manager import (
    get_recent_predictions, load_trends, save_prediction,
    update_trends_from_ai,
)
from ..utils.config import load_prompt, load_settings
from ..utils.json_repair import parse_json_response
from .ai_client import call_ai


def generate_insights(signals: List[Dict]) -> Optional[List[Dict]]:
    """Generate insight + implication for each signal.

    Args:
        signals: List of signal dicts from signal_extractor

    Returns:
        List of enriched signal dicts with insight/implication added,
        or None on failure.
    """
    if not signals:
        return []

    settings = load_settings()
    max_insights = settings.get("analysis", {}).get("daily_max_insights", 8)

    # Use all signals for insight generation (prompt asks to cover every signal)
    top_signals = signals[:max_insights]
    print(f"  Insight generation: {len(top_signals)} signals")

    # Build context
    recent_predictions = get_recent_predictions(weeks=4)
    if recent_predictions:
        history_context = json.dumps(recent_predictions[-10:], ensure_ascii=False, indent=2)
    else:
        history_context = "No past predictions yet (first run)."

    trends_data = load_trends()
    trends = trends_data.get("trends", [])
    if trends:
        trends_context = json.dumps(
            [{"name": t["name"], "trajectory": t.get("trajectory"),
              "signal_count": t.get("signal_count", 0)}
             for t in trends[:15]],
            ensure_ascii=False, indent=2
        )
    else:
        trends_context = "No trends tracked yet."

    signals_text = json.dumps(
        [{"index": i, "title": s.get("title", ""), "signal_text": s.get("signal_text", ""),
          "tags": s.get("tags", []), "signal_strength": s.get("signal_strength", 0)}
         for i, s in enumerate(top_signals)],
        ensure_ascii=False, indent=2
    )

    prompt = load_prompt(
        "insight_generation",
        history_context=history_context,
        trends_context=trends_context,
        signals=signals_text,
    )

    print(f"  Insight generation: {len(top_signals)} signals → Sonnet")

    response = call_ai(prompt, "insight_generation", use_sonnet=True, max_tokens=4096)
    if not response:
        print("  Insight generation failed: no AI response")
        return None

    parsed = parse_json_response(response)
    if not parsed:
        print("  Insight generation failed: could not parse JSON")
        return None

    insights = parsed.get("insights", [])

    # Merge insights back into signals
    enriched = []
    for insight_item in insights:
        idx = insight_item.get("signal_index", -1)
        if 0 <= idx < len(top_signals):
            signal = top_signals[idx].copy()
            signal["insight"] = insight_item.get("insight", "")
            signal["implication"] = insight_item.get("implication", "")
            signal["category"] = insight_item.get("category", "")
            enriched.append(signal)

            # Save implication as prediction for self-correction
            if signal.get("implication"):
                from datetime import datetime
                from zoneinfo import ZoneInfo
                from ..utils.config import get_timezone
                tz = ZoneInfo(get_timezone())
                save_prediction({
                    "date": datetime.now(tz).isoformat(),
                    "prediction_text": signal["implication"],
                    "category": signal.get("category", ""),
                    "source_signal": signal.get("title", ""),
                })

    print(f"  Generated {len(enriched)} insights")
    return enriched


def generate_trend_summary(signals: List[Dict]) -> str:
    """Generate a cross-source trend summary paragraph.

    Args:
        signals: Today's signals

    Returns:
        Trend summary paragraph as plain text
    """
    if not signals:
        return ""

    trends_data = load_trends()
    trends = trends_data.get("trends", [])

    signals_summary = "\n".join(
        f"- [{s.get('signal_strength', 0):.1f}] {s.get('title', '')}: {s.get('signal_text', '')[:100]}"
        for s in signals[:10]
    )

    if trends:
        trends_summary = "\n".join(
            f"- {t['name']}: {t.get('trajectory', 'stable')} (signals: {t.get('signal_count', 0)})"
            for t in trends[:10]
        )
    else:
        trends_summary = "No trends tracked yet."

    # Count weeks of data
    from ..memory.manager import load_weekly_signals
    weekly = load_weekly_signals()
    weeks = len(weekly.get("archived_weeks", [])) + (1 if weekly.get("days") else 0)

    prompt = load_prompt(
        "trend_summary",
        n=len(signals),
        weeks=max(weeks, 1),
        signals_summary=signals_summary,
        trends_summary=trends_summary,
    )

    response = call_ai(prompt, "trend_summary", use_sonnet=True, max_tokens=2048)
    return response.strip() if response else ""


def update_trends(signals: List[Dict]):
    """Update the trend database based on today's signals.

    Uses Haiku for cost-efficient structured updates.
    """
    if not signals:
        return

    settings = load_settings()
    max_trends = settings.get("memory", {}).get("max_trends", 50)

    trends_data = load_trends()
    current_trends = json.dumps(
        trends_data.get("trends", []),
        ensure_ascii=False, indent=2
    )

    today_signals = json.dumps(
        [{"title": s.get("title", ""), "tags": s.get("tags", []),
          "signal_strength": s.get("signal_strength", 0)}
         for s in signals],
        ensure_ascii=False, indent=2
    )

    prompt = load_prompt(
        "trend_update",
        max_trends=max_trends,
        current_trends=current_trends,
        today_signals=today_signals,
    )

    response = call_ai(prompt, "trend_update", use_sonnet=False, max_tokens=4096)
    if not response:
        print("  Trend update failed: no AI response")
        return

    parsed = parse_json_response(response)
    if not parsed:
        print("  Trend update failed: could not parse JSON")
        return

    update_trends_from_ai(parsed)
    print("  Trends updated successfully")
