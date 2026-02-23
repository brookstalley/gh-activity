"""Tests for gh_activity.report."""

from datetime import date

import pandas as pd

from gh_activity.report import (
    build_dataframe,
    aggregate,
    compute_metrics,
    generate_report,
)


class TestBuildDataframe:
    def test_empty_commits(self):
        df = build_dataframe([], date(2025, 1, 1), date(2025, 1, 10))
        assert len(df) == 10
        assert df["commits"].sum() == 0

    def test_commits_aggregated_daily(self):
        commits = [
            {"sha": "a", "date": "2025-01-05", "additions": 10, "deletions": 2, "repo": "r"},
            {"sha": "b", "date": "2025-01-05", "additions": 5, "deletions": 1, "repo": "r"},
            {"sha": "c", "date": "2025-01-07", "additions": 20, "deletions": 10, "repo": "r"},
        ]
        df = build_dataframe(commits, date(2025, 1, 1), date(2025, 1, 10))
        assert len(df) == 10
        assert df.loc["2025-01-05", "commits"] == 2
        assert df.loc["2025-01-05", "additions"] == 15
        assert df.loc["2025-01-07", "deletions"] == 10
        assert df.loc["2025-01-03", "commits"] == 0

    def test_missing_stats_default_to_zero(self):
        commits = [{"sha": "a", "date": "2025-01-05", "repo": "r"}]
        df = build_dataframe(commits, date(2025, 1, 1), date(2025, 1, 10))
        assert df.loc["2025-01-05", "additions"] == 0


class TestAggregate:
    def test_day_passthrough(self):
        commits = [
            {"sha": "a", "date": "2025-01-05", "additions": 10, "deletions": 2, "repo": "r"},
        ]
        daily = build_dataframe(commits, date(2025, 1, 1), date(2025, 1, 10))
        agg = aggregate(daily, "day")
        assert len(agg) == len(daily)

    def test_week_aggregation(self):
        commits = [
            {"sha": f"s{i}", "date": f"2025-01-{d:02d}", "additions": 1, "deletions": 0, "repo": "r"}
            for i, d in enumerate(range(1, 15))
        ]
        daily = build_dataframe(commits, date(2025, 1, 1), date(2025, 1, 14))
        agg = aggregate(daily, "week")
        assert agg["commits"].sum() == 14

    def test_month_aggregation(self):
        commits = [
            {"sha": f"s{i}", "date": f"2025-01-{d:02d}", "additions": 1, "deletions": 0, "repo": "r"}
            for i, d in enumerate(range(1, 32))
        ]
        daily = build_dataframe(commits, date(2025, 1, 1), date(2025, 1, 31))
        agg = aggregate(daily, "month")
        assert agg["commits"].sum() == 31


class TestComputeMetrics:
    def test_metrics(self):
        commits = [
            {"sha": "a", "date": "2025-01-05", "additions": 10, "deletions": 2, "repo": "r"},
            {"sha": "b", "date": "2025-01-07", "additions": 20, "deletions": 5, "repo": "r"},
        ]
        daily = build_dataframe(commits, date(2025, 1, 1), date(2025, 1, 10))
        m = compute_metrics(daily, date(2025, 1, 1), date(2025, 1, 10))
        assert m["total_commits"] == 2
        assert m["total_additions"] == 30
        assert m["total_deletions"] == 7
        assert m["net_lines"] == 23
        assert m["active_days"] == 2
        assert m["total_days"] == 10
        assert m["active_day_pct"] == 20.0

    def test_zero_commits(self):
        daily = build_dataframe([], date(2025, 1, 1), date(2025, 1, 10))
        m = compute_metrics(daily, date(2025, 1, 1), date(2025, 1, 10))
        assert m["total_commits"] == 0
        assert m["active_day_pct"] == 0


class TestGenerateReport:
    def test_generates_html_file(self, tmp_path):
        commits = [
            {"sha": "a", "date": "2025-01-05", "additions": 10, "deletions": 2, "repo": "r", "message": "test"},
        ]
        output = tmp_path / "report.html"
        generate_report(
            commits=commits,
            since=date(2025, 1, 1),
            until=date(2025, 1, 10),
            granularity="week",
            output_path=str(output),
            username="testuser",
        )
        assert output.exists()
        content = output.read_text()
        assert "testuser" in content
        assert "plotly" in content.lower()
        assert "Commits" in content

    def test_generates_with_no_commits(self, tmp_path):
        output = tmp_path / "report.html"
        generate_report(
            commits=[],
            since=date(2025, 1, 1),
            until=date(2025, 1, 10),
            granularity="day",
            output_path=str(output),
            username="testuser",
        )
        assert output.exists()
