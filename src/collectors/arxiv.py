"""Paper collector — HuggingFace Daily Papers.

Uses the HuggingFace Daily Papers API to fetch community-curated trending
papers. This replaces the raw Arxiv API as the primary paper source, providing
much higher signal-to-noise ratio through community upvoting.

API: https://huggingface.co/api/daily_papers
"""

import json
import subprocess
from datetime import datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

from .base import BaseCollector, RawItem
from ..utils.config import load_sources, get_timezone

HF_PAPERS_API = "https://huggingface.co/api/daily_papers"
MIN_UPVOTES = 5  # Only include papers with >= this many upvotes


def _curl_json(url: str, timeout: int = 30) -> Optional[list]:
    """Fetch JSON via curl subprocess."""
    cmd = [
        "/usr/bin/curl", "-sS", "--max-time", str(timeout), "-L",
        "-H", "User-Agent: AI-Frontier-Insight-Bot/1.0",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
        if result.returncode != 0:
            print(f"  Papers: curl failed: {result.stderr.strip()}")
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print(f"  Papers: fetch error: {e}")
        return None


def _fetch_hf_daily_papers(date_str: Optional[str] = None) -> List[dict]:
    """Fetch papers from HuggingFace Daily Papers API.

    Args:
        date_str: Optional date string (YYYY-MM-DD). If None, fetches today's.

    Returns:
        List of paper dicts sorted by upvotes descending.
    """
    url = HF_PAPERS_API
    if date_str:
        url += f"?date={date_str}"

    data = _curl_json(url)
    if not data or not isinstance(data, list):
        return []

    papers = []
    for item in data:
        paper = item.get("paper", item)
        upvotes = paper.get("upvotes", 0)

        # Quality filter: skip low-upvote papers
        if upvotes < MIN_UPVOTES:
            continue

        arxiv_id = paper.get("id", "")
        title = paper.get("title", "").strip()
        summary = paper.get("summary", "").strip()
        ai_summary = paper.get("ai_summary", "").strip()

        authors = [a.get("name", "") for a in paper.get("authors", []) if not a.get("hidden")]

        org = paper.get("organization")
        org_name = org.get("fullname", org.get("name", "")) if org else ""

        papers.append({
            "arxiv_id": arxiv_id,
            "title": title,
            "summary": ai_summary or summary,  # Prefer AI summary (shorter)
            "full_abstract": summary,
            "authors": authors,
            "org": org_name,
            "upvotes": upvotes,
            "published": paper.get("publishedAt", ""),
            "github_repo": paper.get("githubRepo", ""),
            "github_stars": paper.get("githubStars", 0),
            "url": f"https://huggingface.co/papers/{arxiv_id}",
        })

    # Sort by upvotes descending
    papers.sort(key=lambda p: p["upvotes"], reverse=True)
    return papers


class ArxivCollector(BaseCollector):
    """Collects trending AI papers from HuggingFace Daily Papers."""

    source_type = "arxiv"

    def collect(self) -> List[RawItem]:
        sources = load_sources()
        arxiv_config = sources.get("arxiv", {})

        if not arxiv_config.get("enabled", False):
            print("  Paper collector disabled")
            return []

        max_results = arxiv_config.get("max_results", 25)
        print(f"  Papers: fetching HuggingFace Daily Papers...")

        # Fetch today and yesterday (papers may appear with delay)
        tz = ZoneInfo(get_timezone())
        today = datetime.now(tz)
        yesterday = today - timedelta(days=1)

        all_papers = []
        seen_ids = set()

        for date in [today, yesterday]:
            date_str = date.strftime("%Y-%m-%d")
            papers = _fetch_hf_daily_papers(date_str)
            for p in papers:
                if p["arxiv_id"] not in seen_ids:
                    seen_ids.add(p["arxiv_id"])
                    all_papers.append(p)

        # Also fetch without date param (gets latest)
        papers = _fetch_hf_daily_papers()
        for p in papers:
            if p["arxiv_id"] not in seen_ids:
                seen_ids.add(p["arxiv_id"])
                all_papers.append(p)

        # Re-sort by upvotes and limit
        all_papers.sort(key=lambda p: p["upvotes"], reverse=True)
        all_papers = all_papers[:max_results]

        items = []
        for paper in all_papers:
            authors = paper["authors"]
            if len(authors) > 3:
                author_str = ", ".join(authors[:3]) + " et al."
            else:
                author_str = ", ".join(authors)

            # Build content with key metadata
            parts = [f"Authors: {author_str}"]
            if paper["org"]:
                parts.append(f"Organization: {paper['org']}")
            parts.append(f"Upvotes: {paper['upvotes']}")
            if paper["github_stars"]:
                parts.append(f"GitHub Stars: {paper['github_stars']}")

            summary = paper["summary"]
            if len(summary) > 400:
                summary = summary[:400] + "..."
            parts.append(f"Summary: {summary}")

            items.append(RawItem(
                title=paper["title"],
                content=". ".join(parts),
                source_type="arxiv",
                source_name="HuggingFace Papers",
                url=paper["url"],
                published=paper["published"],
                metadata={
                    "authors": authors,
                    "org": paper["org"],
                    "upvotes": paper["upvotes"],
                    "arxiv_id": paper["arxiv_id"],
                    "github_repo": paper["github_repo"],
                    "github_stars": paper["github_stars"],
                },
            ))

        print(f"  Papers: {len(items)} papers (filtered by upvotes >= {MIN_UPVOTES})")
        return items
