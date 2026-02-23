"""Tests for gh_activity.cache."""

import json
from datetime import date

from gh_activity.cache import (
    add_fetched_range,
    compute_gaps,
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
        assert [c["sha"] for c in result] == ["aaa", "bbb"]

    def test_empty_existing(self):
        result = merge_commits([], [{"sha": "a", "repo": "r"}])
        assert len(result) == 1

    def test_empty_new(self):
        result = merge_commits([{"sha": "a", "repo": "r"}], [])
        assert len(result) == 1

    def test_both_empty(self):
        assert merge_commits([], []) == []


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
            "commits": [{"sha": "abc", "repo": "r", "date": "2025-01-01"}],
            "fetched_ranges": [["2025-01-01", "2025-01-31"]],
        }
        save_cache("testuser", data)
        loaded = load_cache("testuser")
        assert loaded == data

    def test_load_missing_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("gh_activity.cache.CACHE_DIR", tmp_path)
        result = load_cache("nonexistent")
        assert result == {"commits": [], "fetched_ranges": []}
