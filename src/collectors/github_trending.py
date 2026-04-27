"""GitHub collector: Trending repos (via Search API) + new releases on watched repos.

Uses GitHub REST API:
- Search API: approximate trending by finding recently created repos with high stars
- Releases API: monitor watched repos for new releases

Requires: GITHUB_TOKEN env var (optional but recommended for higher rate limits).
"""

import os
import time
from datetime import datetime, timedelta
from typing import Dict, List

import requests

from .base import BaseCollector, RawItem
from ..utils.config import load_sources
from ..utils.http import robust_get


def _github_headers() -> Dict[str, str]:
    """Build request headers, with auth token if available."""
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _search_trending_by_topic(topic: str, days_back: int = 7,
                               min_stars: int = 30, limit: int = 10) -> List[Dict]:
    """Search for recently created repos by topic, sorted by stars."""
    date_threshold = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    query = f"topic:{topic} created:>{date_threshold} stars:>={min_stars}"

    resp = robust_get(
        "https://api.github.com/search/repositories",
        headers=_github_headers(),
        params={
            "q": query,
            "sort": "stars",
            "order": "desc",
            "per_page": limit,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("items", [])


def _fetch_releases(repo: str, since_date: str = None,
                     max_releases: int = 3) -> List[Dict]:
    """Fetch recent releases for a repo.

    Args:
        repo: "owner/name" format
        since_date: ISO date string, only return releases published after this
        max_releases: Max releases to return
    """
    resp = robust_get(
        f"https://api.github.com/repos/{repo}/releases",
        headers=_github_headers(),
        params={"per_page": max_releases},
        timeout=15,
    )
    if resp.status_code == 404:
        return []  # Repo has no releases
    resp.raise_for_status()

    releases = []
    for release in resp.json():
        published = release.get("published_at", "")
        if since_date and published < since_date:
            break
        releases.append(release)

    return releases


class GitHubTrendingCollector(BaseCollector):
    """Collects trending AI repos and new releases from watched repos."""

    source_type = "github"

    def __init__(self, days_back: int = 7, min_stars: int = 30):
        self.days_back = days_back
        self.min_stars = min_stars

    def collect(self) -> List[RawItem]:
        sources = load_sources()
        gh_config = sources.get("github", {})

        if not gh_config.get("enabled", False):
            print("  GitHub collector disabled")
            return []

        items = []

        # Part 1: Trending repos by topic
        items.extend(self._collect_trending(gh_config))

        # Part 2: New releases on watched repos
        items.extend(self._collect_releases(gh_config))

        print(f"  GitHub total: {len(items)} items")
        return items

    def _collect_trending(self, config: Dict) -> List[RawItem]:
        """Search for trending repos across configured topics."""
        trending_config = config.get("trending", {})
        topics = trending_config.get("topics", [])
        max_results = trending_config.get("max_results", 30)

        if not topics:
            return []

        print(f"  GitHub Trending: searching {len(topics)} topics...")

        # Deduplicate across topic searches
        seen_repos = set()
        all_items = []

        per_topic = max(5, max_results // len(topics))

        for topic in topics:
            try:
                repos = _search_trending_by_topic(
                    topic=topic,
                    days_back=self.days_back,
                    min_stars=self.min_stars,
                    limit=per_topic,
                )
                for repo in repos:
                    full_name = repo.get("full_name", "")
                    if full_name in seen_repos:
                        continue
                    seen_repos.add(full_name)

                    stars = repo.get("stargazers_count", 0)
                    description = repo.get("description", "") or ""
                    language = repo.get("language", "")
                    repo_topics = repo.get("topics", [])

                    all_items.append(RawItem(
                        title=f"{full_name} ({stars} stars)",
                        content=f"{description} [Language: {language}] [Topics: {', '.join(repo_topics[:5])}]",
                        source_type="github",
                        source_name=f"GitHub Trending ({topic})",
                        url=repo.get("html_url", ""),
                        published=repo.get("created_at", ""),
                        metadata={
                            "stars": stars,
                            "forks": repo.get("forks_count", 0),
                            "language": language,
                            "topics": repo_topics,
                            "sub_source": "trending",
                        },
                    ))

                # Respect rate limit: 30 req/min for search API
                time.sleep(2.5)
            except requests.HTTPError as e:
                if e.response.status_code == 403:
                    print(f"  GitHub rate limited on topic '{topic}', stopping search")
                    break
                print(f"  GitHub search error for '{topic}': {e}")
            except Exception as e:
                print(f"  GitHub search error for '{topic}': {e}")

        # Sort by stars, take top N
        all_items.sort(key=lambda x: x.metadata.get("stars", 0), reverse=True)
        trimmed = all_items[:max_results]
        print(f"  GitHub Trending: {len(trimmed)} repos (from {len(all_items)} found)")
        return trimmed

    def _collect_releases(self, config: Dict) -> List[RawItem]:
        """Check watched repos for new releases."""
        watch_repos = config.get("watch_repos", [])
        if not watch_repos:
            return []

        print(f"  GitHub Releases: checking {len(watch_repos)} repos...")

        # Only get releases from last 48 hours
        since = (datetime.utcnow() - timedelta(hours=48)).isoformat() + "Z"
        items = []

        for repo in watch_repos:
            try:
                releases = _fetch_releases(repo, since_date=since, max_releases=3)
                for release in releases:
                    tag = release.get("tag_name", "")
                    name = release.get("name", "") or tag
                    body = release.get("body", "") or ""
                    # Truncate long release notes
                    if len(body) > 500:
                        body = body[:500] + "..."

                    items.append(RawItem(
                        title=f"{repo} {tag}: {name}",
                        content=body,
                        source_type="github",
                        source_name=f"GitHub Release ({repo})",
                        url=release.get("html_url", ""),
                        published=release.get("published_at", ""),
                        metadata={
                            "repo": repo,
                            "tag": tag,
                            "prerelease": release.get("prerelease", False),
                            "sub_source": "release",
                        },
                    ))
            except Exception as e:
                print(f"  GitHub release check failed for {repo}: {e}")

        print(f"  GitHub Releases: {len(items)} new releases")
        return items
