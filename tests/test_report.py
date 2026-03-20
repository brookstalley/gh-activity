"""Tests for gh_activity.report."""

import json
import re
from datetime import date, timezone

import pandas as pd
from zoneinfo import ZoneInfo

from gh_activity.report import (
    _compute_streak_stats,
    _partial_period_info,
    _first_partial_period_info,
    aggregate,
    build_dataframe,
    compute_metrics,
    compute_period_comparison,
    generate_report,
)


# Helpers
def _ts(day, hour=12):
    """Build a UTC ISO timestamp string for a given day in Jan 2025."""
    return f"2025-01-{day:02d}T{hour:02d}:00:00Z"


def _commits(days, repo="r"):
    """Build commit list with full timestamps for the given day numbers."""
    return [
        {"sha": f"s{d}", "date": _ts(d), "additions": 10, "deletions": 2,
         "repo": repo, "message": f"commit on day {d}"}
        for d in days
    ]


class TestBuildDataframe:
    def test_empty_commits(self):
        daily, commit_df = build_dataframe([], date(2025, 1, 1), date(2025, 1, 10))
        assert len(daily) == 10
        assert daily["commits"].sum() == 0
        assert commit_df.empty

    def test_commits_aggregated_daily(self):
        commits = [
            {"sha": "a", "date": "2025-01-05T10:00:00Z", "additions": 10, "deletions": 2, "repo": "r"},
            {"sha": "b", "date": "2025-01-05T14:00:00Z", "additions": 5, "deletions": 1, "repo": "r"},
            {"sha": "c", "date": "2025-01-07T08:00:00Z", "additions": 20, "deletions": 10, "repo": "r"},
        ]
        daily, commit_df = build_dataframe(commits, date(2025, 1, 1), date(2025, 1, 10))
        assert len(daily) == 10
        assert len(commit_df) == 3

    def test_missing_stats_default_to_zero(self):
        commits = [{"sha": "a", "date": "2025-01-05T12:00:00Z", "repo": "r"}]
        daily, _ = build_dataframe(commits, date(2025, 1, 1), date(2025, 1, 10))
        # Check that the day with commit has 0 additions (missing field)
        jan5 = daily.index[daily.index.normalize() == pd.Timestamp("2025-01-05", tz="UTC")]
        assert daily.loc[jan5, "additions"].iloc[0] == 0

    def test_timezone_shifts_date(self):
        # 2025-01-06T03:00:00Z → Jan 5 at 19:00 in America/Los_Angeles
        commits = [{"sha": "a", "date": "2025-01-06T03:00:00Z", "additions": 5,
                     "deletions": 1, "repo": "r"}]
        tz = ZoneInfo("America/Los_Angeles")
        daily, _ = build_dataframe(commits, date(2025, 1, 1), date(2025, 1, 10), tz=tz)
        jan5 = pd.Timestamp("2025-01-05", tz=tz)
        jan6 = pd.Timestamp("2025-01-06", tz=tz)
        assert daily.loc[jan5, "commits"] == 1
        assert daily.loc[jan6, "commits"] == 0

    def test_returns_commit_df(self):
        commits = _commits([5, 7])
        _, commit_df = build_dataframe(commits, date(2025, 1, 1), date(2025, 1, 10))
        assert len(commit_df) == 2
        assert "date" in commit_df.columns
        assert "sha" in commit_df.columns


class TestAggregate:
    def test_day_passthrough(self):
        daily, _ = build_dataframe(_commits([5]), date(2025, 1, 1), date(2025, 1, 10))
        agg = aggregate(daily, "day")
        assert len(agg) == len(daily)

    def test_week_aggregation(self):
        daily, _ = build_dataframe(
            _commits(range(1, 15)), date(2025, 1, 1), date(2025, 1, 14),
        )
        agg = aggregate(daily, "week")
        assert agg["commits"].sum() == 14

    def test_month_aggregation(self):
        daily, _ = build_dataframe(
            _commits(range(1, 29)), date(2025, 1, 1), date(2025, 1, 28),
        )
        agg = aggregate(daily, "month")
        assert agg["commits"].sum() == 28


class TestComputeMetrics:
    def test_metrics(self):
        daily, _ = build_dataframe(
            _commits([5, 7]), date(2025, 1, 1), date(2025, 1, 10),
        )
        m = compute_metrics(daily, date(2025, 1, 1), date(2025, 1, 10))
        assert m["total_commits"] == 2
        assert m["active_days"] == 2
        assert m["total_days"] == 10
        assert m["active_day_pct"] == 20.0

    def test_zero_commits(self):
        daily, _ = build_dataframe([], date(2025, 1, 1), date(2025, 1, 10))
        m = compute_metrics(daily, date(2025, 1, 1), date(2025, 1, 10))
        assert m["total_commits"] == 0
        assert m["active_day_pct"] == 0

    def test_includes_streak_stats(self):
        daily, _ = build_dataframe(
            _commits([5, 6, 7, 9, 10]), date(2025, 1, 1), date(2025, 1, 10),
        )
        m = compute_metrics(daily, date(2025, 1, 1), date(2025, 1, 10))
        assert "current_streak" in m
        assert "longest_streak" in m
        assert "avg_gap" in m
        assert m["longest_streak"] == 3  # days 5, 6, 7
        assert m["current_streak"] == 2  # days 9, 10


class TestStreakStats:
    def test_no_activity(self):
        daily, _ = build_dataframe([], date(2025, 1, 1), date(2025, 1, 10))
        s = _compute_streak_stats(daily)
        assert s == {"current_streak": 0, "longest_streak": 0, "avg_gap": 0.0}

    def test_single_day(self):
        daily, _ = build_dataframe(_commits([5]), date(2025, 1, 1), date(2025, 1, 10))
        s = _compute_streak_stats(daily)
        # Day 10 (last) has no commits → skip it; day 9 has none → streak 0
        assert s["current_streak"] == 0
        assert s["longest_streak"] == 1
        assert s["avg_gap"] == 0.0

    def test_consecutive_days(self):
        daily, _ = build_dataframe(
            _commits([5, 6, 7, 8, 9]), date(2025, 1, 1), date(2025, 1, 10),
        )
        s = _compute_streak_stats(daily)
        assert s["longest_streak"] == 5

    def test_current_streak_at_end(self):
        daily, _ = build_dataframe(
            _commits([8, 9, 10]), date(2025, 1, 1), date(2025, 1, 10),
        )
        s = _compute_streak_stats(daily)
        assert s["current_streak"] == 3

    def test_current_streak_broken(self):
        daily, _ = build_dataframe(
            _commits([7, 8]), date(2025, 1, 1), date(2025, 1, 10),
        )
        s = _compute_streak_stats(daily)
        assert s["current_streak"] == 0

    def test_current_streak_skips_incomplete_today(self):
        # Commits on days 8, 9 but NOT on day 10 (today/end of range)
        # Streak should be 2, not 0 — today isn't over yet
        daily, _ = build_dataframe(
            _commits([8, 9]), date(2025, 1, 1), date(2025, 1, 10),
        )
        s = _compute_streak_stats(daily)
        assert s["current_streak"] == 2

    def test_current_streak_two_days_gap_is_zero(self):
        # Commits on day 7 but not 8, 9, or 10 — streak is truly broken
        daily, _ = build_dataframe(
            _commits([7]), date(2025, 1, 1), date(2025, 1, 10),
        )
        s = _compute_streak_stats(daily)
        assert s["current_streak"] == 0

    def test_avg_gap(self):
        # Days 2, 5, 8 → gaps of 3, 3 → avg 3.0
        daily, _ = build_dataframe(
            _commits([2, 5, 8]), date(2025, 1, 1), date(2025, 1, 10),
        )
        s = _compute_streak_stats(daily)
        assert s["avg_gap"] == 3.0


class TestPartialPeriod:
    def test_week_partial(self):
        label = pd.Timestamp("2025-01-06")  # Monday (W-MON label)
        is_partial, actual, expected = _partial_period_info(label, "week", date(2025, 1, 2))
        assert is_partial
        assert actual == 3  # Tue Dec 31, Wed Jan 1, Thu Jan 2
        assert expected == 7

    def test_week_complete(self):
        label = pd.Timestamp("2025-01-06")
        is_partial, _, _ = _partial_period_info(label, "week", date(2025, 1, 6))
        assert not is_partial

    def test_month_partial(self):
        label = pd.Timestamp("2025-03-01")
        is_partial, actual, expected = _partial_period_info(label, "month", date(2025, 3, 19))
        assert is_partial
        assert actual == 19
        assert expected == 31

    def test_month_complete(self):
        label = pd.Timestamp("2025-03-01")
        is_partial, _, _ = _partial_period_info(label, "month", date(2025, 3, 31))
        assert not is_partial

    def test_day_granularity_never_partial(self):
        label = pd.Timestamp("2025-01-05")
        is_partial, _, _ = _partial_period_info(label, "day", date(2025, 1, 5))
        assert not is_partial


class TestFirstPartialPeriod:
    def test_week_partial_since_mid_week(self):
        # W-MON label is the Monday. Period is Tue-Mon.
        # If since is Thursday, first period is partial.
        label = pd.Timestamp("2025-01-06")  # Monday
        is_partial, actual, expected = _first_partial_period_info(label, "week", date(2025, 1, 2))
        assert is_partial
        assert expected == 7

    def test_week_complete(self):
        # If since is the Tuesday (period start), it's complete
        label = pd.Timestamp("2025-01-06")  # Monday
        # Period start is Tue Dec 31
        is_partial, _, _ = _first_partial_period_info(label, "week", date(2024, 12, 31))
        assert not is_partial

    def test_month_partial(self):
        label = pd.Timestamp("2025-03-01")
        is_partial, actual, expected = _first_partial_period_info(label, "month", date(2025, 3, 10))
        assert is_partial
        assert actual == 22  # Mar 10 through Mar 31
        assert expected == 31


class TestPeriodComparison:
    def test_no_prior_data(self):
        commits = _commits([5, 6, 7])
        result = compute_period_comparison(commits, date(2025, 1, 5), date(2025, 1, 10))
        # No commits before Jan 5, so should return None
        assert result is None

    def test_with_prior_data(self):
        # Current period: Jan 6-10, Prior period: Jan 1-5
        prior_commits = [
            {"sha": f"p{d}", "date": _ts(d), "additions": 5, "deletions": 1,
             "repo": "r", "message": "prior"}
            for d in range(1, 6)
        ]
        current_commits = _commits([6, 7, 8, 9, 10])
        all_commits = prior_commits + current_commits
        result = compute_period_comparison(all_commits, date(2025, 1, 6), date(2025, 1, 10))
        assert result is not None
        assert result["total_commits"] == 5


class TestGenerateReport:
    def test_generates_html_file(self, tmp_path):
        commits = [
            {"sha": "a", "date": "2025-01-05T12:00:00Z", "additions": 10,
             "deletions": 2, "repo": "r", "message": "test"},
        ]
        output = tmp_path / "report.html"
        generate_report(
            commits=commits,
            since=date(2025, 1, 1),
            until=date(2025, 1, 10),
            granularity="week",
            output_path=str(output),
            username="testuser",
            use_cdn=True,
        )
        assert output.exists()
        content = output.read_text()
        assert "testuser" in content
        assert "plotly" in content.lower()

    def test_json_blob_present(self, tmp_path):
        commits = _commits([5, 7])
        output = tmp_path / "report.html"
        generate_report(
            commits=commits,
            since=date(2025, 1, 1),
            until=date(2025, 1, 10),
            granularity="auto",
            output_path=str(output),
            username="testuser",
            use_cdn=True,
            all_cached_commits=commits,
        )
        content = output.read_text()
        assert 'id="commit-data"' in content
        assert 'id="initial-state"' in content
        # Parse the embedded JSON
        match = re.search(r'id="commit-data">(.*?)</script>', content, re.DOTALL)
        assert match
        # Unescape the safe JSON encoding
        raw = match.group(1).replace("<\\/", "</")
        data = json.loads(raw)
        assert len(data) == 2
        assert data[0]["sha"] == "s5"

    def test_initial_state_embedded(self, tmp_path):
        output = tmp_path / "report.html"
        tz = ZoneInfo("America/New_York")
        generate_report(
            commits=_commits([5]),
            since=date(2025, 1, 1),
            until=date(2025, 1, 10),
            granularity="auto",
            output_path=str(output),
            username="testuser",
            tz=tz,
            use_cdn=True,
        )
        content = output.read_text()
        match = re.search(r'id="initial-state">(.*?)</script>', content, re.DOTALL)
        assert match
        state = json.loads(match.group(1))
        assert state["since"] == "2025-01-01"
        assert state["until"] == "2025-01-10"
        assert state["granularity"] == "auto"
        assert state["timezone"] == "America/New_York"
        assert state["username"] == "testuser"

    def test_controls_present(self, tmp_path):
        output = tmp_path / "report.html"
        generate_report(
            commits=[],
            since=date(2025, 1, 1),
            until=date(2025, 1, 10),
            granularity="week",
            output_path=str(output),
            username="testuser",
            use_cdn=True,
        )
        content = output.read_text()
        assert 'id="ctrl-since"' in content
        assert 'id="ctrl-until"' in content
        assert 'id="ctrl-granularity"' in content
        assert 'id="ctrl-timezone"' in content
        assert '<option value="auto">Auto</option>' in content

    def test_chart_containers_present(self, tmp_path):
        output = tmp_path / "report.html"
        generate_report(
            commits=[],
            since=date(2025, 1, 1),
            until=date(2025, 1, 10),
            granularity="week",
            output_path=str(output),
            username="testuser",
            use_cdn=True,
        )
        content = output.read_text()
        for div_id in [
            "cards", "heatmap-container", "repo-breakdown", "dow-chart",
            "hour-chart", "commits-chart", "lines-chart",
            "active-days-chart", "top-commits",
        ]:
            assert f'id="{div_id}"' in content

    def test_js_application_present(self, tmp_path):
        output = tmp_path / "report.html"
        generate_report(
            commits=_commits([5]),
            since=date(2025, 1, 1),
            until=date(2025, 1, 10),
            granularity="week",
            output_path=str(output),
            username="testuser",
            use_cdn=True,
        )
        content = output.read_text()
        assert "renderAll" in content
        assert "filterCommits" in content
        assert "buildDaily" in content
        assert "Plotly.react" in content

    def test_generates_with_no_commits(self, tmp_path):
        output = tmp_path / "report.html"
        generate_report(
            commits=[],
            since=date(2025, 1, 1),
            until=date(2025, 1, 10),
            granularity="day",
            output_path=str(output),
            username="testuser",
            use_cdn=True,
        )
        assert output.exists()
        content = output.read_text()
        # Should still have a valid JSON blob (empty array)
        match = re.search(r'id="commit-data">(.*?)</script>', content, re.DOTALL)
        assert match
        data = json.loads(match.group(1))
        assert data == []

    def test_dark_mode_css_present(self, tmp_path):
        output = tmp_path / "report.html"
        generate_report(
            commits=[],
            since=date(2025, 1, 1),
            until=date(2025, 1, 10),
            granularity="week",
            output_path=str(output),
            username="testuser",
            use_cdn=True,
        )
        content = output.read_text()
        assert "prefers-color-scheme: dark" in content

    def test_offline_plotly(self, tmp_path):
        output = tmp_path / "report.html"
        generate_report(
            commits=[],
            since=date(2025, 1, 1),
            until=date(2025, 1, 10),
            granularity="week",
            output_path=str(output),
            username="testuser",
            use_cdn=False,
        )
        content = output.read_text()
        # Should NOT have a CDN <script src=...> tag
        assert '<script src="https://cdn.plot.ly' not in content
        # Should have inline Plotly JS (large embedded script)
        assert len(content) > 100_000  # embedded plotly.js is ~3.5MB

    def test_cdn_plotly(self, tmp_path):
        output = tmp_path / "report.html"
        generate_report(
            commits=[],
            since=date(2025, 1, 1),
            until=date(2025, 1, 10),
            granularity="week",
            output_path=str(output),
            username="testuser",
            use_cdn=True,
        )
        content = output.read_text()
        assert "cdn.plot.ly" in content

    def test_embeds_all_cached_commits(self, tmp_path):
        """all_cached_commits (not just filtered commits) should be embedded."""
        filtered = _commits([5])
        all_cached = _commits([3, 5, 7, 9])
        output = tmp_path / "report.html"
        generate_report(
            commits=filtered,
            since=date(2025, 1, 4),
            until=date(2025, 1, 8),
            granularity="day",
            output_path=str(output),
            username="testuser",
            use_cdn=True,
            all_cached_commits=all_cached,
        )
        content = output.read_text()
        match = re.search(r'id="commit-data">(.*?)</script>', content, re.DOTALL)
        raw = match.group(1).replace("<\\/", "</")
        data = json.loads(raw)
        assert len(data) == 4  # all cached, not just filtered

    def test_script_tag_in_message_escaped(self, tmp_path):
        """Commit messages with </script> should not break the HTML."""
        commits = [{"sha": "a", "date": "2025-01-05T12:00:00Z", "repo": "r",
                     "message": "</script><script>alert(1)</script>",
                     "additions": 1, "deletions": 0}]
        output = tmp_path / "report.html"
        generate_report(
            commits=commits,
            since=date(2025, 1, 1),
            until=date(2025, 1, 10),
            granularity="day",
            output_path=str(output),
            username="testuser",
            use_cdn=True,
        )
        content = output.read_text()
        # The raw </script> should be escaped
        match = re.search(r'id="commit-data">(.*?)</script>', content, re.DOTALL)
        assert match
        raw = match.group(1).replace("<\\/", "</")
        data = json.loads(raw)
        assert data[0]["message"] == "</script><script>alert(1)</script>"

    def test_cross_validation_json_matches_commits(self, tmp_path):
        """Verify embedded JSON can be parsed and matches source data."""
        commits = _commits([2, 5, 8])
        output = tmp_path / "report.html"
        generate_report(
            commits=commits,
            since=date(2025, 1, 1),
            until=date(2025, 1, 10),
            granularity="week",
            output_path=str(output),
            username="testuser",
            use_cdn=True,
            all_cached_commits=commits,
        )
        content = output.read_text()
        match = re.search(r'id="commit-data">(.*?)</script>', content, re.DOTALL)
        raw = match.group(1).replace("<\\/", "</")
        embedded = json.loads(raw)

        # Run Python data functions on embedded data for cross-validation
        daily, _ = build_dataframe(embedded, date(2025, 1, 1), date(2025, 1, 10))
        metrics = compute_metrics(daily, date(2025, 1, 1), date(2025, 1, 10))
        assert metrics["total_commits"] == 3
        assert metrics["active_days"] == 3
