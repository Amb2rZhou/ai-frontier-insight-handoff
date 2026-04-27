"""Signal Extractor: Raw items → ranked, deduplicated signals.

Uses Haiku for cost-efficient high-volume filtering.
"""

import json
from typing import Dict, List, Optional

from ..collectors.base import RawItem
from ..memory.manager import load_trends, get_recent_signal_titles
from ..utils.config import load_prompt, load_settings
from ..utils.json_repair import parse_json_response
from .ai_client import call_ai, call_haiku


def _title_similar(a: str, b: str, threshold: float = 0.6) -> bool:
    """Check if two titles are similar enough to be considered duplicates.

    Two-pass check:
    1. Word overlap ratio >= threshold (original logic)
    2. If first 2 meaningful words match (subject + verb), lower threshold to 0.35
       e.g. "Anthropic Restricts ..." vs "Anthropic Restricts ..." = same event
    """
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return False
    overlap = len(words_a & words_b)
    shorter = min(len(words_a), len(words_b))
    ratio = overlap / shorter

    if ratio >= threshold:
        return True

    # Pass 2: if subject+verb match, use relaxed threshold
    list_a = [w.strip(",:;'\"") for w in a.lower().split()]
    list_b = [w.strip(",:;'\"") for w in b.lower().split()]
    if len(list_a) >= 2 and len(list_b) >= 2:
        if list_a[0] == list_b[0] and list_a[1] == list_b[1]:
            return ratio >= 0.35

    return False


def extract_signals(raw_items: List[RawItem]) -> Optional[List[Dict]]:
    """Extract top signals from raw items using AI.

    Args:
        raw_items: List of RawItem from all collectors

    Returns:
        List of signal dicts, or None on failure.
        Each signal: {title, signal_text, signal_strength, sources, tags}
    """
    if not raw_items:
        print("  No raw items to extract signals from")
        return []

    settings = load_settings()
    max_signals = settings.get("analysis", {}).get("daily_max_signals", 15)

    # Load current trends for novelty assessment
    trends_data = load_trends()
    trends = trends_data.get("trends", [])
    if trends:
        trends_context = json.dumps(
            [{"name": t["name"], "trajectory": t.get("trajectory", "stable"),
              "signal_count": t.get("signal_count", 0)}
             for t in trends[:20]],
            ensure_ascii=False, indent=2
        )
    else:
        trends_context = "No trends tracked yet (first run)."

    # Load recent signal titles for dedup
    from ..utils.config import get_timezone
    from datetime import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(get_timezone())
    today = datetime.now(tz).strftime("%Y-%m-%d")
    recent_titles = get_recent_signal_titles(days=3, exclude_date=today)
    if recent_titles:
        recent_context = "\n".join(f"- {t}" for t in recent_titles)
    else:
        recent_context = "（首次运行，无历史记录）"

    # Build compact raw items text for prompt
    raw_items_text = "\n".join(
        f"[{i}] {item.to_compact()}" for i, item in enumerate(raw_items)
    )

    # Build prompt
    prompt = load_prompt(
        "signal_extraction",
        n=len(raw_items),
        max_signals=max_signals,
        trends_context=trends_context,
        recent_signals=recent_context,
        raw_items=raw_items_text,
    )

    print(f"  Signal extraction: {len(raw_items)} items → max {max_signals} signals")
    print(f"  Prompt size: {len(prompt)} chars")

    # Use Haiku for cost efficiency (high volume filtering)
    response = call_ai(prompt, "signal_extraction", use_sonnet=False, max_tokens=8192)
    if not response:
        print("  Signal extraction failed: no AI response")
        return None

    # Parse JSON response
    parsed = parse_json_response(response)
    if not parsed:
        print("  Signal extraction failed: could not parse JSON response")
        return None

    signals = parsed.get("signals", [])
    print(f"  Extracted {len(signals)} signals")

    # Dedup: remove signals whose title is too similar to recent signals
    if recent_titles:
        recent_lower = [t.lower().strip().lstrip("[update] ") for t in recent_titles]
        before = len(signals)
        deduped = []
        for s in signals:
            title = s.get("title", "").lower().strip().lstrip("[update] ")
            if any(_title_similar(title, r) for r in recent_lower):
                print(f"  Dedup: removed '{s.get('title', '')[:50]}' (similar to recent)")
            else:
                deduped.append(s)
        signals = deduped
        if before > len(signals):
            print(f"  Dedup: {before} → {len(signals)} signals")

    # Post-filter: remove arxiv/paper signals with no identifiable institution
    _TOP_ORGS = [
        "google", "deepmind", "openai", "anthropic", "meta", "fair",
        "microsoft", "apple", "nvidia", "huggingface",
        "bytedance", "字节", "tencent", "腾讯", "alibaba", "阿里", "baidu", "百度",
        "huawei", "华为", "sensetime", "商汤", "salesforce",
        "samsung", "sony", "adobe", "ibm", "intel", "amazon", "aws",
        "mit", "stanford", "cmu", "carnegie", "berkeley", "清华", "北大", "中科院",
        "peking university", "tsinghua", "shanghai jiao tong", "上海交大",
        "princeton", "harvard", "yale", "oxford", "cambridge", "eth zurich",
        "toronto", "montreal", "mila",
    ]
    before_filter = len(signals)
    filtered = []
    filtered_out = []  # keep for backfill if needed
    for s in signals:
        text = (s.get("signal_text", "") + " " + s.get("title", "")).lower()
        sources = [src.get("name", "").lower() for src in s.get("sources", [])]
        is_paper = any("arxiv" in src or "paper" in src or "huggingface.co/papers" in src
                       for src in sources) or "arxiv" in text
        if is_paper:
            has_org = any(org in text for org in _TOP_ORGS)
            if not has_org:
                print(f"  Filter: removed paper '{s.get('title', '')[:50]}' (no known institution)")
                filtered_out.append(s)
                continue
        filtered.append(s)
    signals = filtered
    if before_filter > len(signals):
        print(f"  Paper filter: {before_filter} → {len(signals)} signals")

    # Sort by signal_strength descending
    signals.sort(key=lambda s: s.get("signal_strength", 0), reverse=True)

    # Trim to max output count
    final_count = settings.get("analysis", {}).get("daily_output_signals", 10)
    if len(signals) > final_count:
        signals = signals[:final_count]

    # Attach source URLs from raw items
    for signal in signals:
        indices = signal.get("raw_item_indices", [])
        if not signal.get("sources"):
            signal["sources"] = []
            for idx in indices:
                if 0 <= idx < len(raw_items):
                    item = raw_items[idx]
                    signal["sources"].append({
                        "name": item.source_name,
                        "url": item.url,
                    })

    return signals
