"""HuggingFace Hub collector.

Fetches trending models, new models, and trending Spaces via the HF REST API.
No authentication required (anonymous: 500 req/5min).

API docs: https://huggingface.co/docs/hub/en/api
"""

from datetime import datetime, timedelta
from typing import Dict, List

import requests

from .base import BaseCollector, RawItem
from ..utils.config import load_sources
from ..utils.http import robust_get

HF_API_BASE = "https://huggingface.co/api"


def _hf_get(endpoint: str, params: Dict = None, limit: int = 20) -> List[Dict]:
    """Make a GET request to HuggingFace API."""
    url = f"{HF_API_BASE}/{endpoint}"
    if params is None:
        params = {}
    params.setdefault("limit", str(limit))

    resp = robust_get(url, params=params, timeout=30, headers={
        "User-Agent": "AI-Frontier-Insight-Bot/1.0",
    })
    resp.raise_for_status()
    return resp.json()


def _fetch_trending_models(limit: int = 20) -> List[Dict]:
    """Get currently trending models sorted by trending score."""
    return _hf_get("models", {
        "sort": "trendingScore",
        "direction": "-1",
        "limit": str(limit),
    })


def _fetch_new_models(limit: int = 15) -> List[Dict]:
    """Get recently created models."""
    return _hf_get("models", {
        "sort": "createdAt",
        "direction": "-1",
        "limit": str(limit),
    })


def _fetch_trending_spaces(limit: int = 15) -> List[Dict]:
    """Get currently trending Spaces."""
    return _hf_get("spaces", {
        "sort": "trendingScore",
        "direction": "-1",
        "limit": str(limit),
    })


class HuggingFaceCollector(BaseCollector):
    """Collects trending models, new models, and trending Spaces from HuggingFace."""

    source_type = "huggingface"

    def __init__(self, trending_models: int = 15, new_models: int = 10,
                 trending_spaces: int = 10):
        self.trending_models_limit = trending_models
        self.new_models_limit = new_models
        self.trending_spaces_limit = trending_spaces

    def collect(self) -> List[RawItem]:
        sources = load_sources()
        hf_config = sources.get("huggingface", {})

        # Default to enabled if not explicitly disabled
        if hf_config.get("enabled") is False:
            print("  HuggingFace collector disabled")
            return []

        items = []

        # 1. Trending models
        print("  HuggingFace: fetching trending models...")
        try:
            models = _fetch_trending_models(self.trending_models_limit)
            for m in models:
                model_id = m.get("id", m.get("modelId", ""))
                likes = m.get("likes", 0)
                downloads = m.get("downloads", 0)
                pipeline = m.get("pipeline_tag", "")
                tags = m.get("tags", [])

                items.append(RawItem(
                    title=f"{model_id} (Trending)",
                    content=self._model_description(m),
                    source_type="huggingface",
                    source_name="HuggingFace Trending Models",
                    url=f"https://huggingface.co/{model_id}",
                    published=m.get("created_at", m.get("createdAt", "")),
                    metadata={
                        "likes": likes,
                        "downloads": downloads,
                        "pipeline_tag": pipeline,
                        "tags": tags[:10],
                        "sub_source": "trending_model",
                    },
                ))
            print(f"  HuggingFace trending models: {len(models)}")
        except Exception as e:
            print(f"  HuggingFace trending models error: {e}")

        # 2. New models (last 48h with some traction)
        print("  HuggingFace: fetching new models...")
        try:
            new_models = _fetch_new_models(self.new_models_limit)
            cutoff = (datetime.utcnow() - timedelta(hours=48)).isoformat()
            seen_ids = {i.url for i in items}  # Dedup with trending

            for m in new_models:
                model_id = m.get("id", m.get("modelId", ""))
                url = f"https://huggingface.co/{model_id}"
                if url in seen_ids:
                    continue

                created = m.get("created_at", m.get("createdAt", ""))
                # Skip if older than cutoff (API returns newest first, so we can break)
                if created and created < cutoff:
                    break

                likes = m.get("likes", 0)
                downloads = m.get("downloads", 0)

                # Only include if it has some traction (>0 likes or >100 downloads)
                if likes == 0 and downloads < 100:
                    continue

                items.append(RawItem(
                    title=f"{model_id} (New)",
                    content=self._model_description(m),
                    source_type="huggingface",
                    source_name="HuggingFace New Models",
                    url=url,
                    published=created,
                    metadata={
                        "likes": likes,
                        "downloads": downloads,
                        "pipeline_tag": m.get("pipeline_tag", ""),
                        "sub_source": "new_model",
                    },
                ))
            print(f"  HuggingFace new models: {len([i for i in items if i.metadata.get('sub_source') == 'new_model'])}")
        except Exception as e:
            print(f"  HuggingFace new models error: {e}")

        # 3. Trending Spaces
        print("  HuggingFace: fetching trending spaces...")
        try:
            spaces = _fetch_trending_spaces(self.trending_spaces_limit)
            for s in spaces:
                space_id = s.get("id", "")
                likes = s.get("likes", 0)
                sdk = s.get("sdk", "")

                items.append(RawItem(
                    title=f"{space_id} (Trending Space)",
                    content=f"SDK: {sdk}. Likes: {likes}.",
                    source_type="huggingface",
                    source_name="HuggingFace Trending Spaces",
                    url=f"https://huggingface.co/spaces/{space_id}",
                    published=s.get("created_at", s.get("createdAt", "")),
                    metadata={
                        "likes": likes,
                        "sdk": sdk,
                        "sub_source": "trending_space",
                    },
                ))
            print(f"  HuggingFace trending spaces: {len(spaces)}")
        except Exception as e:
            print(f"  HuggingFace trending spaces error: {e}")

        print(f"  HuggingFace total: {len(items)} items")
        return items

    @staticmethod
    def _model_description(m: Dict) -> str:
        """Build a concise description string for a model."""
        parts = []
        pipeline = m.get("pipeline_tag", "")
        if pipeline:
            parts.append(f"Task: {pipeline}")

        likes = m.get("likes", 0)
        downloads = m.get("downloads", 0)
        parts.append(f"Likes: {likes}, Downloads: {downloads}")

        tags = m.get("tags", [])
        # Filter useful tags (skip generic ones)
        useful_tags = [t for t in tags if t not in ("pytorch", "safetensors", "transformers", "en")]
        if useful_tags:
            parts.append(f"Tags: {', '.join(useful_tags[:8])}")

        author = m.get("author", "")
        if author:
            parts.append(f"Author: {author}")

        return ". ".join(parts)
