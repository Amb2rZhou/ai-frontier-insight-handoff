"""RSS feed collector.

Fetches and parses RSS feeds in parallel using ThreadPoolExecutor.
Adapted from daily-news-digest parse_feed() + fetch_raw_news().
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List

import feedparser
import requests

from .base import BaseCollector, RawItem
from ..utils.config import load_sources
from ..utils.http import robust_get


def _parse_single_feed(feed_url: str, feed_name: str, group: str,
                        cutoff: datetime = None) -> List[RawItem]:
    """Parse a single RSS feed and return recent items as RawItem list."""
    items = []
    if cutoff is None:
        cutoff = datetime.now() - timedelta(hours=24)

    try:
        # Use requests for fetching (handles SSL/timeouts better than feedparser)
        resp = robust_get(
            feed_url,
            timeout=15,
            headers={"User-Agent": "AI-Frontier-Insight-Bot/1.0"},
        )
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)

        source_name = feed_name or feed.feed.get("title", feed_url)

        for entry in feed.entries[:20]:  # Limit entries per feed
            # Parse published time
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime(*entry.published_parsed[:6])
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                published = datetime(*entry.updated_parsed[:6])

            # Skip if too old or no date
            if published and published < cutoff:
                continue

            title = entry.get("title", "").strip()
            description = entry.get("summary", entry.get("description", ""))
            if description:
                description = description[:500].strip()

            items.append(RawItem(
                title=title,
                content=description,
                source_type="rss",
                source_name=source_name,
                url=entry.get("link", ""),
                published=published.isoformat() if published else "",
                metadata={"group": group, "feed_url": feed_url},
            ))
    except requests.RequestException as e:
        print(f"  Warning: RSS fetch failed for {feed_name}: {e}")
    except Exception as e:
        print(f"  Warning: RSS parse failed for {feed_name}: {e}")

    return items


class RSSCollector(BaseCollector):
    """Collects items from configured RSS feeds in parallel."""

    source_type = "rss"

    def __init__(self, cutoff: datetime = None, max_per_source: int = 5):
        """
        Args:
            cutoff: Only include articles published after this time.
                    Defaults to 24 hours ago.
            max_per_source: Maximum items to keep per feed source.
        """
        self.cutoff = cutoff or (datetime.now() - timedelta(hours=24))
        self.max_per_source = max_per_source

    def collect(self) -> List[RawItem]:
        """Fetch all enabled RSS feeds in parallel and return RawItem list."""
        sources = load_sources()
        rss_config = sources.get("rss", {})

        if not rss_config.get("enabled", False):
            print("  RSS collector disabled")
            return []

        feeds = rss_config.get("feeds", [])
        enabled_feeds = [f for f in feeds if f.get("enabled", True)]
        print(f"  RSS: {len(enabled_feeds)} feeds enabled")

        # Parallel fetch
        all_items: List[RawItem] = []
        items_by_source: Dict[str, List[RawItem]] = {}
        stats = {"success": 0, "empty": 0, "failed": 0}

        start = time.time()

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(
                    _parse_single_feed,
                    f["url"],
                    f.get("name", ""),
                    f.get("group", ""),
                    self.cutoff,
                ): f
                for f in enabled_feeds
            }

            for future in as_completed(futures, timeout=90):
                feed_cfg = futures[future]
                try:
                    items = future.result(timeout=30)
                    if items:
                        stats["success"] += 1
                        for item in items:
                            src = item.source_name
                            if src not in items_by_source:
                                items_by_source[src] = []
                            items_by_source[src].append(item)
                    else:
                        stats["empty"] += 1
                except TimeoutError:
                    stats["failed"] += 1
                    print(f"  Warning: {feed_cfg.get('name', '?')} timed out (30s)")
                except Exception as e:
                    stats["failed"] += 1
                    print(f"  Warning: {feed_cfg.get('name', '?')} error: {e}")

        elapsed = time.time() - start
        print(f"  RSS fetch: {elapsed:.1f}s | success={stats['success']} empty={stats['empty']} failed={stats['failed']}")

        # Limit per source for diversity
        for source_name, items in items_by_source.items():
            items.sort(key=lambda x: x.published, reverse=True)
            all_items.extend(items[:self.max_per_source])

        # Sort all by published time (newest first)
        all_items.sort(key=lambda x: x.published, reverse=True)

        print(f"  RSS total: {len(all_items)} items from {len(items_by_source)} sources")
        # Show top sources
        top = sorted(items_by_source.items(), key=lambda x: -len(x[1]))[:5]
        if top:
            print(f"  Top RSS sources: {[(s, len(i)) for s, i in top]}")

        return all_items
