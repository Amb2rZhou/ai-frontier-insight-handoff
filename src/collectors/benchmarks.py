"""Benchmark leaderboard collector.

Monitors five leaderboards via public APIs:
1. Open LLM Leaderboard — HuggingFace datasets-server filter API
2. SWE-bench Verified — GitHub raw JSON from swe-bench.github.io
3. ARC-AGI-2 — arcprize.org evaluations JSON
4. OSWorld — os-world.github.io verified results xlsx
5. Terminal-Bench 2.0 — tbench.ai SSR leaderboard data

Stores top-N snapshots in memory/benchmark_snapshots.json and generates
RawItem signals when rankings change (new #1, new entries, significant moves).
"""

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from .base import BaseCollector, RawItem
from ..utils.config import MEMORY_DIR, get_timezone

SNAPSHOT_FILE = os.path.join(MEMORY_DIR, "benchmark_snapshots.json")

# HuggingFace datasets-server filter API (max 100 rows/request)
# Filter for avg>40 to reduce data (~300 rows), then sort locally for top N
LEADERBOARD_FILTER_URL = (
    "https://datasets-server.huggingface.co/filter"
    "?dataset=open-llm-leaderboard/contents"
    "&config=default&split=train"
    "&where=%22Average+%E2%AC%86%EF%B8%8F%22+%3E+40"
)

TOP_N = 20  # Track top N models
SCORE_THRESHOLD = 40  # Only fetch models scoring above this
RANK_CHANGE_THRESHOLD = 5  # Only report rank changes >= this

# SWE-bench leaderboard JSON (from GitHub Pages source repo)
SWEBENCH_URL = (
    "https://raw.githubusercontent.com/SWE-bench/swe-bench.github.io"
    "/master/data/leaderboards.json"
)
SWEBENCH_TOP_N = 15  # Track top N entries for SWE-bench Verified

# ARC-AGI-2 leaderboard JSON (from arcprize.org)
ARCAGI_URL = "https://arcprize.org/media/data/leaderboard/evaluations.json"
ARCAGI_DATASET = "v2_Semi_Private"  # Main ARC-AGI-2 benchmark split
ARCAGI_TOP_N = 15

# OSWorld Verified leaderboard (xlsx from GitHub Pages)
OSWORLD_URL = "https://os-world.github.io/static/data/osworld_verified_results.xlsx"
OSWORLD_TOP_N = 15

# Terminal-Bench 2.0 leaderboard (SSR data from tbench.ai)
TBENCH_URL = "https://www.tbench.ai/leaderboard/terminal-bench/2.0"
TBENCH_TOP_N = 15

# 关注机构关键词 — 这些机构的模型上榜即报
WATCHED_ORGS = [
    "openai", "google", "deepmind", "anthropic", "meta", "llama",
    "bytedance", "doubao", "字节", "tencent", "腾讯", "alibaba", "qwen", "阿里",
    "baidu", "百度", "nvidia", "microsoft", "apple",
]


def _curl_json(url: str, timeout: int = 60) -> Optional[dict]:
    """Fetch JSON via curl subprocess (bypasses LibreSSL issues)."""
    cmd = [
        "/usr/bin/curl", "-sS", "--max-time", str(timeout), "-L",
        "-H", "User-Agent: AI-Frontier-Insight-Bot/1.0",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
        if result.returncode != 0:
            print(f"  Benchmark: curl failed: {result.stderr.strip()}")
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print(f"  Benchmark: fetch error: {e}")
        return None


def _curl_text(url: str, timeout: int = 60) -> Optional[str]:
    """Fetch text content via curl subprocess."""
    cmd = [
        "/usr/bin/curl", "-sS", "--max-time", str(timeout), "-L",
        "-H", "User-Agent: AI-Frontier-Insight-Bot/1.0",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
        if result.returncode != 0:
            print(f"  Benchmark: curl failed: {result.stderr.strip()}")
            return None
        return result.stdout
    except subprocess.TimeoutExpired as e:
        print(f"  Benchmark: fetch error: {e}")
        return None


def _curl_binary(url: str, output_path: str, timeout: int = 60) -> bool:
    """Download binary file via curl subprocess."""
    cmd = [
        "/usr/bin/curl", "-sS", "--max-time", str(timeout), "-L",
        "-H", "User-Agent: AI-Frontier-Insight-Bot/1.0",
        "-o", output_path,
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def _load_snapshot() -> dict:
    """Load previous benchmark snapshot."""
    try:
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_snapshot(data: dict):
    """Save benchmark snapshot."""
    os.makedirs(MEMORY_DIR, exist_ok=True)
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _fetch_leaderboard_rows() -> List[Dict]:
    """Fetch all rows with avg>40 from the leaderboard, paginating as needed."""
    all_rows = []
    offset = 0
    page_size = 100

    while True:
        url = f"{LEADERBOARD_FILTER_URL}&offset={offset}&length={page_size}"
        data = _curl_json(url)
        if not data or "error" in data:
            if data:
                print(f"  Benchmark: API error: {data.get('error')}")
            break

        rows = data.get("rows", [])
        all_rows.extend(rows)

        total = data.get("num_rows_total", 0)
        offset += page_size
        if offset >= total or not rows:
            break

    return all_rows


def _extract_top_models(rows: List[Dict]) -> List[Dict]:
    """Extract and sort top N models from API rows."""
    if not rows:
        return []

    models = []
    for row_wrapper in rows:
        row = row_wrapper.get("row", {})
        avg_score = row.get("Average ⬆️")
        if avg_score is None:
            continue

        try:
            avg_score = float(avg_score)
        except (TypeError, ValueError):
            continue

        model_name = row.get("fullname", row.get("model_name_for_query", ""))
        if not model_name:
            continue

        models.append({
            "name": model_name,
            "score": round(avg_score, 2),
            "architecture": row.get("Architecture", ""),
            "type": row.get("Type", ""),
            "precision": row.get("Precision", ""),
            "params_b": row.get("#Params (B)", ""),
        })

    models.sort(key=lambda m: m["score"], reverse=True)
    return models[:TOP_N]


def _is_watched_org(model_name: str) -> bool:
    """Check if a model belongs to a watched organization."""
    name_lower = model_name.lower()
    return any(org in name_lower for org in WATCHED_ORGS)


def _diff_snapshots(old_top: List[Dict], new_top: List[Dict]) -> List[Dict]:
    """Compare old and new top-N lists, return list of change dicts.

    Reporting rules:
    1. New #1 — always report
    2. New entry in top 5 — always report
    3. New entry from watched org — always report (regardless of rank)
    4. Rank jump >= RANK_CHANGE_THRESHOLD — report
    5. Dropped / small rank changes — skip
    """
    changes = []

    old_by_name = {m["name"]: (i, m) for i, m in enumerate(old_top)}

    # Check for new #1
    if new_top and old_top:
        if new_top[0]["name"] != old_top[0]["name"]:
            changes.append({
                "type": "new_leader",
                "model": new_top[0]["name"],
                "score": new_top[0]["score"],
                "previous_leader": old_top[0]["name"],
                "previous_score": old_top[0]["score"],
            })

    # New entries and rank changes
    for rank, model in enumerate(new_top):
        name = model["name"]
        if name not in old_by_name:
            # New entry: report if top 5 or watched org
            if rank < 5 or _is_watched_org(name):
                changes.append({
                    "type": "new_entry",
                    "model": name,
                    "score": model["score"],
                    "rank": rank + 1,
                })
        else:
            old_rank = old_by_name[name][0]
            rank_delta = old_rank - rank  # positive = climbed
            if rank_delta >= RANK_CHANGE_THRESHOLD:
                changes.append({
                    "type": "rank_change",
                    "model": name,
                    "score": model["score"],
                    "old_rank": old_rank + 1,
                    "new_rank": rank + 1,
                    "delta": rank_delta,
                })

    # Dropped models — skip (not reported)

    return changes


def _fetch_swebench_verified() -> List[Dict]:
    """Fetch SWE-bench Verified leaderboard entries from GitHub."""
    data = _curl_json(SWEBENCH_URL, timeout=60)
    if not data:
        return []

    boards = data.get("leaderboards", [])
    for board in boards:
        if board.get("name") == "Verified":
            results = board.get("results", [])
            # Sort by resolved % descending
            results.sort(key=lambda x: float(x.get("resolved", 0)), reverse=True)
            top = []
            for entry in results[:SWEBENCH_TOP_N]:
                top.append({
                    "name": entry.get("name", ""),
                    "score": round(float(entry.get("resolved", 0)), 1),
                    "date": entry.get("date", ""),
                })
            return top

    return []


def _fetch_arcagi2() -> List[Dict]:
    """Fetch ARC-AGI-2 leaderboard from arcprize.org JSON."""
    data = _curl_json(ARCAGI_URL, timeout=60)
    if not data:
        return []

    # data is a list of evaluation entries
    entries = data if isinstance(data, list) else data.get("evaluations", data.get("data", []))
    if not isinstance(entries, list):
        return []

    top = []
    for entry in entries:
        if entry.get("datasetId") != ARCAGI_DATASET:
            continue
        score = entry.get("score")
        if score is None:
            continue
        try:
            score = round(float(score) * 100, 1)  # 0-1 → percentage
        except (TypeError, ValueError):
            continue

        name = entry.get("modelId", "")
        if not name:
            continue

        top.append({
            "name": name,
            "score": score,
        })

    top.sort(key=lambda m: m["score"], reverse=True)
    return top[:ARCAGI_TOP_N]


def _fetch_osworld_verified() -> List[Dict]:
    """Fetch OSWorld Verified leaderboard from xlsx file."""
    try:
        import openpyxl
    except ImportError:
        print("  Benchmark: openpyxl not installed, skipping OSWorld")
        return []

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        if not _curl_binary(OSWORLD_URL, tmp_path):
            return []

        wb = openpyxl.load_workbook(tmp_path, read_only=True)
        ws = wb.active
        if ws is None:
            return []

        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []

        # Find column indices from header row
        header = [str(h).strip() if h else "" for h in rows[0]]
        try:
            model_idx = header.index("Model")
        except ValueError:
            # Try case-insensitive
            model_idx = next((i for i, h in enumerate(header) if h.lower() == "model"), 0)

        # Find success rate column
        sr_idx = None
        for i, h in enumerate(header):
            if "success rate" in h.lower() or "success_rate" in h.lower():
                sr_idx = i
                break
        if sr_idx is None:
            sr_idx = next((i for i, h in enumerate(header) if h.lower() == "score"), None)
        if sr_idx is None:
            print(f"  Benchmark: OSWorld - cannot find score column in {header}")
            return []

        # Find institution column
        inst_idx = None
        for i, h in enumerate(header):
            if "institution" in h.lower():
                inst_idx = i
                break

        top = []
        for row in rows[1:]:
            if len(row) <= max(model_idx, sr_idx):
                continue
            name = row[model_idx]
            score = row[sr_idx]
            if not name or score is None:
                continue
            try:
                score = round(float(score) * 100, 1) if float(score) <= 1 else round(float(score), 1)
            except (TypeError, ValueError):
                continue

            entry = {"name": str(name), "score": score}
            if inst_idx is not None and inst_idx < len(row) and row[inst_idx]:
                entry["institution"] = str(row[inst_idx])
            top.append(entry)

        wb.close()
        top.sort(key=lambda m: m["score"], reverse=True)
        return top[:OSWORLD_TOP_N]
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _fetch_terminal_bench() -> List[Dict]:
    """Fetch Terminal-Bench 2.0 leaderboard from tbench.ai SSR data."""
    html = _curl_text(TBENCH_URL, timeout=60)
    if not html:
        return []

    # Extract Next.js RSC streaming data
    matches = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL)
    if not matches:
        print("  Benchmark: Terminal-Bench - no RSC data found")
        return []

    combined = "".join(matches)
    # Unescape double-encoded JSON
    combined = combined.replace('\\"', '"').replace('\\\\', '\\').replace('\\n', '\n')

    # Extract objects with accuracy field
    objs = re.findall(r'\{[^{}]*"accuracy":[0-9.]+[^{}]*\}', combined)
    if not objs:
        print("  Benchmark: Terminal-Bench - no accuracy data found")
        return []

    entries = []
    for obj_str in objs:
        try:
            obj = json.loads(obj_str)
        except json.JSONDecodeError:
            continue
        if "agent" not in obj or "accuracy" not in obj:
            continue

        accuracy = obj.get("accuracy", 0)
        agent_name = obj.get("agentName", obj.get("agent", ""))
        agent_org = obj.get("agentOrganization", "")
        model_names = obj.get("modelNames", obj.get("model", []))
        if isinstance(model_names, list):
            model_str = " + ".join(model_names) if model_names else ""
        else:
            model_str = str(model_names)

        display_name = f"{agent_name} ({model_str})" if model_str else agent_name
        if agent_org:
            display_name = f"{agent_org}/{display_name}"

        entries.append({
            "name": display_name,
            "score": round(float(accuracy) * 100, 1),
            "verified": obj.get("verified", False),
        })

    # Sort by score descending, prefer verified
    entries.sort(key=lambda m: (m["score"], m.get("verified", False)), reverse=True)

    # Deduplicate: keep best score per agent display name
    seen = set()
    top = []
    for entry in entries:
        if entry["name"] not in seen:
            seen.add(entry["name"])
            top.append(entry)
        if len(top) >= TBENCH_TOP_N:
            break

    return top


class BenchmarkCollector(BaseCollector):
    """Monitors benchmark leaderboards for ranking changes."""

    source_type = "benchmark"

    def collect(self) -> List[RawItem]:
        items = []
        snapshot = _load_snapshot()
        tz = ZoneInfo(get_timezone())
        now_iso = datetime.now(tz).isoformat()

        collectors = [
            ("Open LLM", self._collect_open_llm),
            ("SWE-bench", self._collect_swebench),
            ("ARC-AGI-2", self._collect_arcagi2),
            ("OSWorld", self._collect_osworld),
            ("Terminal-Bench", self._collect_terminal_bench),
        ]
        for name, collector_fn in collectors:
            try:
                items.extend(collector_fn(snapshot, now_iso))
            except Exception as e:
                print(f"  Benchmark: {name} collector crashed: {e}")

        _save_snapshot(snapshot)
        print(f"  Benchmark total: {len(items)} items")
        return items

    def _collect_open_llm(self, snapshot: dict, now_iso: str) -> List[RawItem]:
        """Collect Open LLM Leaderboard changes."""
        items = []
        print("  Benchmark: fetching Open LLM Leaderboard...")
        rows = _fetch_leaderboard_rows()
        if not rows:
            print("  Benchmark: failed to fetch Open LLM Leaderboard")
            return []

        print(f"  Benchmark: fetched {len(rows)} rows (avg>{SCORE_THRESHOLD})")
        new_top = _extract_top_models(rows)
        if not new_top:
            print("  Benchmark: no models extracted")
            return []

        print(f"  Benchmark: Open LLM leader: {new_top[0]['name']} @ {new_top[0]['score']}")

        old_top = snapshot.get("open_llm_leaderboard", {}).get("top_models", [])
        board_name = "Open LLM Leaderboard"
        board_url = "https://huggingface.co/spaces/open-llm-leaderboard/open_llm_leaderboard"

        if old_top:
            changes = _diff_snapshots(old_top, new_top)
            if changes:
                print(f"  Benchmark: Open LLM {len(changes)} changes")
                for c in changes:
                    item = self._change_to_item(c, now_iso, board_name, board_url, TOP_N)
                    if item:
                        items.append(item)
            else:
                print("  Benchmark: Open LLM no changes")
        else:
            leader = new_top[0]
            top5 = ", ".join(f"{m['name']} ({m['score']})" for m in new_top[:5])
            items.append(RawItem(
                title=f"Open LLM Leaderboard: {leader['name']} 领跑 ({leader['score']}分)",
                content=f"Open LLM Leaderboard 当前 Top 5: {top5}",
                source_type="benchmark",
                source_name=board_name,
                url=board_url,
                published=now_iso,
                metadata={"sub_source": "open_llm_leaderboard", "change_type": "initial"},
            ))
            print("  Benchmark: Open LLM first run")

        snapshot["open_llm_leaderboard"] = {"top_models": new_top, "updated": now_iso}
        return items

    def _collect_swebench(self, snapshot: dict, now_iso: str) -> List[RawItem]:
        """Collect SWE-bench Verified changes."""
        items = []
        print("  Benchmark: fetching SWE-bench Verified...")
        new_top = _fetch_swebench_verified()
        if not new_top:
            print("  Benchmark: failed to fetch SWE-bench")
            return []

        print(f"  Benchmark: SWE-bench leader: {new_top[0]['name']} @ {new_top[0]['score']}%")

        old_top = snapshot.get("swebench_verified", {}).get("top_models", [])
        board_name = "SWE-bench Verified"
        board_url = "https://www.swebench.com/verified.html"

        if old_top:
            changes = _diff_snapshots(old_top, new_top)
            if changes:
                print(f"  Benchmark: SWE-bench {len(changes)} changes")
                for c in changes:
                    item = self._change_to_item(c, now_iso, board_name, board_url, SWEBENCH_TOP_N, unit="%")
                    if item:
                        items.append(item)
            else:
                print("  Benchmark: SWE-bench no changes")
        else:
            leader = new_top[0]
            top5 = ", ".join(f"{m['name']} ({m['score']}%)" for m in new_top[:5])
            items.append(RawItem(
                title=f"SWE-bench Verified: {leader['name']} 领跑 ({leader['score']}%)",
                content=f"SWE-bench Verified 当前 Top 5: {top5}",
                source_type="benchmark",
                source_name=board_name,
                url=board_url,
                published=now_iso,
                metadata={"sub_source": "swebench_verified", "change_type": "initial"},
            ))
            print("  Benchmark: SWE-bench first run")

        snapshot["swebench_verified"] = {"top_models": new_top, "updated": now_iso}
        return items

    def _collect_arcagi2(self, snapshot: dict, now_iso: str) -> List[RawItem]:
        """Collect ARC-AGI-2 leaderboard changes."""
        items = []
        print("  Benchmark: fetching ARC-AGI-2...")
        new_top = _fetch_arcagi2()
        if not new_top:
            print("  Benchmark: failed to fetch ARC-AGI-2")
            return []

        print(f"  Benchmark: ARC-AGI-2 leader: {new_top[0]['name']} @ {new_top[0]['score']}%")

        old_top = snapshot.get("arc_agi_2", {}).get("top_models", [])
        board_name = "ARC-AGI-2"
        board_url = "https://arcprize.org/leaderboard"

        if old_top:
            changes = _diff_snapshots(old_top, new_top)
            if changes:
                print(f"  Benchmark: ARC-AGI-2 {len(changes)} changes")
                for c in changes:
                    item = self._change_to_item(c, now_iso, board_name, board_url, ARCAGI_TOP_N, unit="%")
                    if item:
                        items.append(item)
            else:
                print("  Benchmark: ARC-AGI-2 no changes")
        else:
            leader = new_top[0]
            top5 = ", ".join(f"{m['name']} ({m['score']}%)" for m in new_top[:5])
            items.append(RawItem(
                title=f"ARC-AGI-2: {leader['name']} 领跑 ({leader['score']}%)",
                content=f"ARC-AGI-2 当前 Top 5: {top5}",
                source_type="benchmark",
                source_name=board_name,
                url=board_url,
                published=now_iso,
                metadata={"sub_source": "arc_agi_2", "change_type": "initial"},
            ))
            print("  Benchmark: ARC-AGI-2 first run")

        snapshot["arc_agi_2"] = {"top_models": new_top, "updated": now_iso}
        return items

    def _collect_osworld(self, snapshot: dict, now_iso: str) -> List[RawItem]:
        """Collect OSWorld Verified leaderboard changes."""
        items = []
        print("  Benchmark: fetching OSWorld Verified...")
        new_top = _fetch_osworld_verified()
        if not new_top:
            print("  Benchmark: failed to fetch OSWorld")
            return []

        print(f"  Benchmark: OSWorld leader: {new_top[0]['name']} @ {new_top[0]['score']}%")

        old_top = snapshot.get("osworld", {}).get("top_models", [])
        board_name = "OSWorld"
        board_url = "https://os-world.github.io/"

        if old_top:
            changes = _diff_snapshots(old_top, new_top)
            if changes:
                print(f"  Benchmark: OSWorld {len(changes)} changes")
                for c in changes:
                    item = self._change_to_item(c, now_iso, board_name, board_url, OSWORLD_TOP_N, unit="%")
                    if item:
                        items.append(item)
            else:
                print("  Benchmark: OSWorld no changes")
        else:
            leader = new_top[0]
            top5 = ", ".join(f"{m['name']} ({m['score']}%)" for m in new_top[:5])
            items.append(RawItem(
                title=f"OSWorld: {leader['name']} 领跑 ({leader['score']}%)",
                content=f"OSWorld Verified 当前 Top 5: {top5}",
                source_type="benchmark",
                source_name=board_name,
                url=board_url,
                published=now_iso,
                metadata={"sub_source": "osworld", "change_type": "initial"},
            ))
            print("  Benchmark: OSWorld first run")

        snapshot["osworld"] = {"top_models": new_top, "updated": now_iso}
        return items

    def _collect_terminal_bench(self, snapshot: dict, now_iso: str) -> List[RawItem]:
        """Collect Terminal-Bench 2.0 leaderboard changes."""
        items = []
        print("  Benchmark: fetching Terminal-Bench 2.0...")
        new_top = _fetch_terminal_bench()
        if not new_top:
            print("  Benchmark: failed to fetch Terminal-Bench")
            return []

        print(f"  Benchmark: Terminal-Bench leader: {new_top[0]['name']} @ {new_top[0]['score']}%")

        old_top = snapshot.get("terminal_bench", {}).get("top_models", [])
        board_name = "Terminal-Bench 2.0"
        board_url = "https://www.tbench.ai/leaderboard/terminal-bench/2.0"

        if old_top:
            changes = _diff_snapshots(old_top, new_top)
            if changes:
                print(f"  Benchmark: Terminal-Bench {len(changes)} changes")
                for c in changes:
                    item = self._change_to_item(c, now_iso, board_name, board_url, TBENCH_TOP_N, unit="%")
                    if item:
                        items.append(item)
            else:
                print("  Benchmark: Terminal-Bench no changes")
        else:
            leader = new_top[0]
            top5 = ", ".join(f"{m['name']} ({m['score']}%)" for m in new_top[:5])
            items.append(RawItem(
                title=f"Terminal-Bench 2.0: {leader['name']} 领跑 ({leader['score']}%)",
                content=f"Terminal-Bench 2.0 当前 Top 5: {top5}",
                source_type="benchmark",
                source_name=board_name,
                url=board_url,
                published=now_iso,
                metadata={"sub_source": "terminal_bench", "change_type": "initial"},
            ))
            print("  Benchmark: Terminal-Bench first run")

        snapshot["terminal_bench"] = {"top_models": new_top, "updated": now_iso}
        return items

    @staticmethod
    def _change_to_item(change: Dict, now_iso: str, board_name: str,
                        board_url: str, top_n: int, unit: str = "分") -> Optional[RawItem]:
        """Convert a change dict to a RawItem signal."""
        ctype = change["type"]
        sub = board_name.lower().replace(" ", "_").replace("-", "_")

        if ctype == "new_leader":
            return RawItem(
                title=f"{board_name} 新榜首: {change['model']} ({change['score']}{unit})",
                content=(
                    f"{change['model']} 以 {change['score']}{unit} 登顶 {board_name}，"
                    f"超越前任榜首 {change['previous_leader']} ({change['previous_score']}{unit})"
                ),
                source_type="benchmark",
                source_name=board_name,
                url=board_url,
                published=now_iso,
                metadata={"sub_source": sub, "change_type": "new_leader"},
            )

        if ctype == "new_entry":
            return RawItem(
                title=f"{board_name}: {change['model']} 新进 Top {top_n} (第{change['rank']}名, {change['score']}{unit})",
                content=f"{change['model']} 首次进入 {board_name} Top {top_n}，排名第 {change['rank']}，得分 {change['score']}{unit}",
                source_type="benchmark",
                source_name=board_name,
                url=board_url,
                published=now_iso,
                metadata={"sub_source": sub, "change_type": "new_entry"},
            )

        if ctype == "rank_change":
            return RawItem(
                title=f"{board_name}: {change['model']} 上升{change['delta']}名 → 第{change['new_rank']}",
                content=(
                    f"{change['model']} 在 {board_name} 排名从第 {change['old_rank']} "
                    f"上升至第 {change['new_rank']}，当前得分 {change['score']}{unit}"
                ),
                source_type="benchmark",
                source_name=board_name,
                url=board_url,
                published=now_iso,
                metadata={"sub_source": sub, "change_type": "rank_change"},
            )

        return None
