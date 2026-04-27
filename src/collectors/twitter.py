"""Twitter/X collector — reads from x-monitor pipeline JSON.

x-monitor runs locally via launchd, scrapes an X List page with Playwright,
and writes structured JSON to data/x-monitor/{date}.json in this repo.
Quality/engagement/relevance filtering is done upstream in x-monitor.
This collector only applies a time window filter (24h).
"""

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List

from .base import BaseCollector, RawItem

# x-monitor 推送数据到本仓库的位置
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "x-monitor"


class TwitterCollector(BaseCollector):
    """Collects tweets from x-monitor's local pipeline output."""

    source_type = "twitter"

    def __init__(self, hours: int = 24):
        """
        Args:
            hours: Only include tweets published within this many hours.
        """
        self.hours = hours

    def _load_file(self, filepath: Path) -> list:
        """Load tweets from a single pipeline JSON file."""
        if not filepath.exists():
            return []
        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            return data.get("tweets", [])
        except (json.JSONDecodeError, OSError):
            return []

    def collect(self) -> List[RawItem]:
        """Read today + yesterday's x-monitor JSON, filter to last N hours."""
        today = date.today()
        yesterday = today - timedelta(days=1)

        # 读取今天和昨天的文件，合并去重
        all_tweets = {}
        for d in [yesterday, today]:
            for t in self._load_file(DATA_DIR / f"{d.isoformat()}.json"):
                tid = t.get("id", "")
                if tid and tid not in all_tweets:
                    all_tweets[tid] = t

        if not all_tweets:
            print(f"  Twitter: no data files for {yesterday} ~ {today}")
            return []

        # 仅时间过滤（质量/互动/语义过滤已在 x-monitor 上游完成）
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.hours)
        items = []
        skipped_old = 0
        for t in all_tweets.values():
            username = t.get("username", "unknown")
            text = t.get("text", "")
            if not text:
                continue

            # 时间过滤：丢弃超过 N 小时的推文
            ts = t.get("timestamp", "")
            if ts:
                try:
                    pub_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if pub_dt < cutoff:
                        skipped_old += 1
                        continue
                except (ValueError, TypeError):
                    pass

            items.append(RawItem(
                title=f"@{username}",
                content=text,
                source_type="twitter",
                source_name=f"@{username}",
                url=t.get("url", ""),
                published=ts,
                metadata={
                    "tweet_id": t.get("id", ""),
                    "images": t.get("images", []),
                    "likes": t.get("likes", 0),
                    "retweets": t.get("retweets", 0),
                    "views": t.get("views", 0),
                    "user_bio": t.get("user_bio", ""),
                },
            ))

        accounts = len(set(item.source_name for item in items))
        print(f"  Twitter: {len(items)} tweets from {accounts} accounts "
              f"(filtered {skipped_old} older than {self.hours}h)")
        return items
