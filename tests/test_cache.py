"""Tests for gh_activity.cache."""

import json
from datetime import date

from gh_activity.cache import (
    add_fetched_range,
    compute_gaps,
    invalidate_stale_timestamps,
    merge_commits,
    load_cache,
    save_cache,
)


class TestMergeCommits:
    def test_no_duplicates(self):
        existing = [{"sha": "aaa", "repo": "r"}]
        new = [{"sha": "bbb", "repo": "r"}]
        result = merge_commits(existing, new)
        assert len(result) == 2

    def test_deduplicates(self):
        existing = [{"sha": "aaa", "repo": "r"}]
        new = [{"sha": "aaa", "repo": "r"}, {"sha": "bbb", "repo": "r"}]
        result = merge_commits(existing, new)
        assert len(result) == 2

    def test_empty_existing(self):
        result = merge_commits([], [{"sha": "a", "repo": "r"}])
        assert len(result) == 1

    def test_empty_new(self):
        result = merge_commits([{"sha": "a", "repo": "r"}], [])
        assert len(result) == 1

    def test_both_empty(self):
        assert merge_commits([], []) == []

    def test_updates_date_on_existing(self):
        existing = [{"sha": "aaa", "repo": "r", "date": "2025-01-05T17:00:00Z",
                      "additions": 10, "deletions": 2}]
        new = [{"sha": "aaa", "repo": "r", "date": "2025-01-03T09:00:00Z",
                 "message": "updated msg"}]
        result = merge_commits(existing, new)
        assert len(result) == 1
        assert result[0]["date"] == "2025-01-03T09:00:00Z"  # date updated
        assert result[0]["additions"] == 10  # line stats preserved


class TestInvalidateStaleTimestamps:
    def test_noop_when_fresh(self):
        data = {
            "commits": [
                {"sha": "a", "date": "2025-01-01T12:00:00Z"},
                {"sha": "b", "date": "2025-01-02T08:30:00Z"},
            ],
            "fetched_ranges": [["2025-01-01", "2025-01-31"]],
        }
        result = invalidate_stale_timestamps(data)
        assert len(result["commits"]) == 2
        assert len(result["fetched_ranges"]) == 1

    def test_clears_when_stale(self):
        data = {
            "commits": [
                {"sha": "a", "date": "2025-01-01"},  # stale
                {"sha": "b", "date": "2025-01-02T08:30:00Z"},  # fresh
            ],
            "fetched_ranges": [["2025-01-01", "2025-01-31"]],
        }
        result = invalidate_stale_timestamps(data)
        # Only the fresh commit survives
        assert len(result["commits"]) == 1
        assert result["commits"][0]["sha"] == "b"
        # Ranges cleared to trigger re-fetch
        assert result["fetched_ranges"] == []

    def test_all_stale(self):
        data = {
            "commits": [{"sha": "a", "date": "2025-01-01"}],
            "fetched_ranges": [["2025-01-01", "2025-01-31"]],
        }
        result = invalidate_stale_timestamps(data)
        assert result["commits"] == []
        assert result["fetched_ranges"] == []

    def test_empty_cache(self):
        data = {"commits": [], "fetched_ranges": []}
        result = invalidate_stale_timestamps(data)
        assert result["commits"] == []


class TestAddFetchedRange:
    def test_single_range(self):
        result = add_fetched_range([], "2025-01-01", "2025-01-31")
        assert result == [["2025-01-01", "2025-01-31"]]

    def test_non_overlapping_ranges(self):
        ranges = [["2025-01-01", "2025-01-15"]]
        result = add_fetched_range(ranges, "2025-02-01", "2025-02-28")
        assert len(result) == 2

    def test_overlapping_ranges_merge(self):
        ranges = [["2025-01-01", "2025-01-20"]]
        result = add_fetched_range(ranges, "2025-01-15", "2025-02-10")
        assert result == [["2025-01-01", "2025-02-10"]]

    def test_adjacent_ranges_merge(self):
        ranges = [["2025-01-01", "2025-01-15"]]
        result = add_fetched_range(ranges, "2025-01-16", "2025-01-31")
        assert result == [["2025-01-01", "2025-01-31"]]

    def test_contained_range(self):
        ranges = [["2025-01-01", "2025-01-31"]]
        result = add_fetched_range(ranges, "2025-01-10", "2025-01-20")
        assert result == [["2025-01-01", "2025-01-31"]]


class TestComputeGaps:
    def test_no_fetched_ranges(self):
        gaps = compute_gaps([], date(2025, 1, 1), date(2025, 1, 31))
        assert gaps == [(date(2025, 1, 1), date(2025, 1, 31))]

    def test_fully_covered(self):
        ranges = [["2025-01-01", "2025-01-31"]]
        gaps = compute_gaps(ranges, date(2025, 1, 1), date(2025, 1, 31))
        assert gaps == []

    def test_gap_at_start(self):
        ranges = [["2025-01-15", "2025-01-31"]]
        gaps = compute_gaps(ranges, date(2025, 1, 1), date(2025, 1, 31))
        assert gaps == [(date(2025, 1, 1), date(2025, 1, 14))]

    def test_gap_at_end(self):
        ranges = [["2025-01-01", "2025-01-15"]]
        gaps = compute_gaps(ranges, date(2025, 1, 1), date(2025, 1, 31))
        assert gaps == [(date(2025, 1, 16), date(2025, 1, 31))]

    def test_gap_in_middle(self):
        ranges = [["2025-01-01", "2025-01-10"], ["2025-01-20", "2025-01-31"]]
        gaps = compute_gaps(ranges, date(2025, 1, 1), date(2025, 1, 31))
        assert gaps == [(date(2025, 1, 11), date(2025, 1, 19))]

    def test_ranges_outside_desired(self):
        ranges = [["2024-06-01", "2024-12-31"]]
        gaps = compute_gaps(ranges, date(2025, 1, 1), date(2025, 1, 31))
        assert gaps == [(date(2025, 1, 1), date(2025, 1, 31))]


class TestLoadSaveCache:
    def test_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gh_activity.cache.CACHE_DIR", tmp_path)
        data = {
            "commits": [{"sha": "abc", "repo": "r", "date": "2025-01-01T12:00:00Z"}],
            "fetched_ranges": [["2025-01-01", "2025-01-31"]],
        }
        save_cache("testuser", data)
        loaded = load_cache("testuser")
        assert loaded["commits"] == data["commits"]
        assert loaded["fetched_ranges"] == data["fetched_ranges"]

    def test_load_missing_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gh_activity.cache.CACHE_DIR", tmp_path)
        result = load_cache("nonexistent")
        assert result["commits"] == []
        assert result["fetched_ranges"] == []

    def test_outdated_version_preserves_commits(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gh_activity.cache.CACHE_DIR", tmp_path)
        # Save with an old version
        data = {
            "commits": [{"sha": "abc", "repo": "r", "date": "2025-01-01T12:00:00Z",
                          "additions": 10, "deletions": 2}],
            "fetched_ranges": [["2025-01-01", "2025-01-31"]],
            "version": 1,
        }
        path = tmp_path / "testuser.json"
        import json
        with open(path, "w") as f:
            json.dump(data, f)
        result = load_cache("testuser")
        # Commits preserved (keeps expensive line stats), ranges cleared for re-search
        assert len(result["commits"]) == 1
        assert result["commits"][0]["additions"] == 10
        assert result["fetched_ranges"] == []
