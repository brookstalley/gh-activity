"""JSON cache for GitHub activity data.

Cache file: ~/.cache/gh-activity/{username}.json
Stores commits and tracks which date ranges have been fetched.
"""

import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any


CACHE_DIR = Path.home() / ".cache" / "gh-activity"
CACHE_VERSION = 3  # v2: author-date; v3: fix search pagination bug that dropped commits


def cache_path(username: str) -> Path:
    return CACHE_DIR / f"{username}.json"


def load_cache(username: str) -> dict:
    """Load cache from disk. Returns empty structure if missing or outdated."""
    path = cache_path(username)
    if not path.exists():
        return {"commits": [], "fetched_ranges": [], "version": CACHE_VERSION}
    with open(path) as f:
        data = json.load(f)
    if data.get("version") != CACHE_VERSION:
        # Keep commits (preserves expensive line stats), clear ranges to trigger
        # re-search so merge_commits can update metadata (e.g., author dates)
        return {
            "commits": data.get("commits", []),
            "fetched_ranges": [],
            "version": CACHE_VERSION,
        }
    return data


def save_cache(username: str, data: dict) -> None:
    """Save cache to disk."""
    data["version"] = CACHE_VERSION
    path = cache_path(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def invalidate_stale_timestamps(cache_data: dict) -> dict:
    """Detect and invalidate cached commits that lack full timestamps.

    Old cache entries store date-only strings (10 chars). Full ISO timestamps
    are 20+ chars. When stale entries are found, they are removed and
    fetched_ranges is cleared so the data will be re-fetched.
    """
    commits = cache_data.get("commits", [])
    has_stale = any(len(c.get("date", "")) <= 10 for c in commits)
    if not has_stale:
        return cache_data
    # Remove stale commits and clear ranges to trigger re-fetch
    fresh = [c for c in commits if len(c.get("date", "")) > 10]
    return {"commits": fresh, "fetched_ranges": []}


def merge_commits(existing: list[dict], new: list[dict]) -> list[dict]:
    """Merge new commits into existing, deduplicating by SHA.

    When a SHA exists in both, updates metadata (date, message) from new
    while preserving existing line stats (additions, deletions).
    """
    by_sha = {c["sha"]: c for c in existing}
    for c in new:
        if c["sha"] in by_sha:
            # Update metadata from newer search results, preserve line stats
            if "date" in c:
                by_sha[c["sha"]]["date"] = c["date"]
            if c.get("message"):
                by_sha[c["sha"]]["message"] = c["message"]
        else:
            by_sha[c["sha"]] = c
    return list(by_sha.values())


def add_fetched_range(ranges: list[list[str]], start: str, end: str) -> list[list[str]]:
    """Add a date range and merge overlapping/adjacent ranges.

    Ranges are [start, end] pairs as ISO date strings.
    """
    new_start = date.fromisoformat(start)
    new_end = date.fromisoformat(end)

    parsed: list[tuple[date, date]] = []
    for r in ranges:
        parsed.append((date.fromisoformat(r[0]), date.fromisoformat(r[1])))
    parsed.append((new_start, new_end))

    return _merge_ranges(parsed)


def _merge_ranges(ranges: list[tuple[date, date]]) -> list[list[str]]:
    """Merge overlapping/adjacent date ranges."""
    if not ranges:
        return []
    sorted_ranges = sorted(ranges, key=lambda r: r[0])
    merged: list[tuple[date, date]] = [sorted_ranges[0]]
    for start, end in sorted_ranges[1:]:
        prev_start, prev_end = merged[-1]
        # Adjacent or overlapping (1-day gap counts as adjacent)
        if start <= prev_end + timedelta(days=1):
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return [[s.isoformat(), e.isoformat()] for s, e in merged]


def compute_gaps(
    fetched_ranges: list[list[str]],
    desired_start: date,
    desired_end: date,
) -> list[tuple[date, date]]:
    """Compute date ranges within [desired_start, desired_end] not yet fetched.

    Returns list of (start, end) tuples representing gaps.
    """
    if not fetched_ranges:
        return [(desired_start, desired_end)]

    # Parse and sort existing ranges
    parsed: list[tuple[date, date]] = []
    for r in fetched_ranges:
        rs = date.fromisoformat(r[0])
        re_ = date.fromisoformat(r[1])
        # Clip to desired range
        if re_ < desired_start or rs > desired_end:
            continue
        parsed.append((max(rs, desired_start), min(re_, desired_end)))

    if not parsed:
        return [(desired_start, desired_end)]

    parsed.sort(key=lambda r: r[0])

    gaps: list[tuple[date, date]] = []
    cursor = desired_start

    for rs, re_ in parsed:
        if cursor < rs:
            gaps.append((cursor, rs - timedelta(days=1)))
        cursor = max(cursor, re_ + timedelta(days=1))

    if cursor <= desired_end:
        gaps.append((cursor, desired_end))

    return gaps
