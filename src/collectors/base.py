"""Base collector interfaces and data models."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class RawItem:
    """A single raw item collected from any source.

    This is the universal intermediate representation before signal extraction.
    """
    title: str
    content: str                    # Description / abstract / tweet text
    source_type: str                # "twitter" | "github" | "arxiv" | "rss"
    source_name: str                # e.g. "Sam Altman (@sama)", "HuggingFace Blog"
    url: str = ""
    published: str = ""             # ISO datetime string
    metadata: Dict = field(default_factory=dict)  # source-specific extra fields

    def to_dict(self) -> Dict:
        return {
            "title": self.title,
            "content": self.content,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "url": self.url,
            "published": self.published,
            "metadata": self.metadata,
        }

    def to_compact(self) -> str:
        """Compact string representation for prompt input (saves tokens)."""
        parts = [f"[{self.source_type}] {self.source_name}"]
        if self.title:
            parts.append(f"Title: {self.title}")
        if self.content:
            # Truncate long content for prompt efficiency
            content = self.content[:300]
            if len(self.content) > 300:
                content += "..."
            parts.append(f"Content: {content}")
        if self.url:
            parts.append(f"URL: {self.url}")
        if self.published:
            parts.append(f"Time: {self.published}")
        # 用户简介（推文）
        user_bio = self.metadata.get("user_bio", "")
        if user_bio:
            parts.append(f"Author Bio: {user_bio[:150]}")
        # 互动数据（推文）
        likes = self.metadata.get("likes", 0)
        views = self.metadata.get("views", 0)
        if likes or views:
            parts.append(f"Engagement: {likes} likes, {views} views")
        return " | ".join(parts)


class BaseCollector(ABC):
    """Abstract base class for all data collectors."""

    @abstractmethod
    def collect(self) -> List[RawItem]:
        """Collect raw items from the source.

        Returns:
            List of RawItem instances
        """
        ...

    @property
    @abstractmethod
    def source_type(self) -> str:
        """Return the source type identifier (e.g. 'twitter', 'rss')."""
        ...
