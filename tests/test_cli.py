"""Tests for gh_activity.cli."""

from datetime import date, timedelta
from unittest.mock import patch, MagicMock

from gh_activity.cli import parse_args, main, resolve_granularity


class TestParseArgs:
    def test_defaults(self):
        args = parse_args([])
        assert args.granularity is None  # auto-resolved later
        assert args.output == "gh-activity-report.html"
        assert args.refresh is False
        assert args.username is None
        assert args.timezone is None
        assert args.cdn is False
        assert args.until == date.today()
        assert args.since == date.today() - timedelta(days=182)

    def test_custom_args(self):
        args = parse_args([
            "--since", "2025-06-01",
            "--until", "2025-12-31",
            "--granularity", "month",
            "--output", "custom.html",
            "--refresh",
            "--username", "octocat",
            "--timezone", "America/Los_Angeles",
            "--cdn",
        ])
        assert args.since == date(2025, 6, 1)
        assert args.until == date(2025, 12, 31)
        assert args.granularity == "month"
        assert args.output == "custom.html"
        assert args.refresh is True
        assert args.username == "octocat"
        assert args.timezone == "America/Los_Angeles"
        assert args.cdn is True

    def test_day_granularity(self):
        args = parse_args(["--granularity", "day"])
        assert args.granularity == "day"


class TestResolveGranularity:
    def test_short_range_is_day(self):
        assert resolve_granularity(date(2025, 3, 1), date(2025, 3, 15)) == "day"

    def test_medium_range_is_week(self):
        assert resolve_granularity(date(2025, 1, 1), date(2025, 4, 1)) == "week"

    def test_long_range_is_month(self):
        assert resolve_granularity(date(2024, 1, 1), date(2025, 1, 1)) == "month"

    def test_exactly_30_days_is_week(self):
        assert resolve_granularity(date(2025, 1, 1), date(2025, 1, 31)) == "week"

    def test_exactly_180_days_is_week(self):
        assert resolve_granularity(date(2025, 1, 1), date(2025, 6, 30)) == "week"


class TestMain:
    @patch("gh_activity.cli.generate_report")
    @patch("gh_activity.cli.fetch_commit_stats")
    @patch("gh_activity.cli.search_commits")
    @patch("gh_activity.cli.load_cache")
    @patch("gh_activity.cli.save_cache")
    def test_full_pipeline_with_username(
        self, mock_save, mock_load, mock_search, mock_stats, mock_report
    ):
        mock_load.return_value = {"commits": [], "fetched_ranges": []}
        mock_search.return_value = [
            {"sha": "a", "repo": "r", "date": "2025-06-15T12:00:00Z", "message": "m"},
        ]

        main(["--username", "octocat", "--since", "2025-06-01", "--until", "2025-06-30"])

        mock_search.assert_called_once()
        mock_stats.assert_called_once()
        mock_report.assert_called_once()
        mock_save.assert_called_once()

    @patch("gh_activity.cli.generate_report")
    @patch("gh_activity.cli.search_commits")
    @patch("gh_activity.cli.load_cache")
    @patch("gh_activity.cli.save_cache")
    def test_cached_data_skips_fetch(
        self, mock_save, mock_load, mock_search, mock_report
    ):
        mock_load.return_value = {
            "commits": [
                {"sha": "a", "repo": "r", "date": "2025-06-15T12:00:00Z", "message": "m",
                 "additions": 10, "deletions": 2},
            ],
            "fetched_ranges": [["2025-06-01", "2025-06-30"]],
        }

        main(["--username", "octocat", "--since", "2025-06-01", "--until", "2025-06-30"])

        mock_search.assert_not_called()
        mock_report.assert_called_once()

    @patch("gh_activity.cli.generate_report")
    @patch("gh_activity.cli.fetch_commit_stats")
    @patch("gh_activity.cli.search_commits")
    @patch("gh_activity.cli.load_cache")
    @patch("gh_activity.cli.save_cache")
    def test_refresh_ignores_cache(
        self, mock_save, mock_load, mock_search, mock_stats, mock_report
    ):
        mock_load.return_value = {
            "commits": [{"sha": "old", "repo": "r", "date": "2025-06-15T12:00:00Z", "message": "m"}],
            "fetched_ranges": [["2025-06-01", "2025-06-30"]],
        }
        mock_search.return_value = [
            {"sha": "new", "repo": "r", "date": "2025-06-15T12:00:00Z", "message": "m"},
        ]

        main([
            "--username", "octocat",
            "--since", "2025-06-01",
            "--until", "2025-06-30",
            "--refresh",
        ])

        # With --refresh, load_cache is still called but its result is ignored
        mock_search.assert_called_once()

    @patch("gh_activity.cli.generate_report")
    @patch("gh_activity.cli.fetch_commit_stats")
    @patch("gh_activity.cli.search_commits")
    @patch("gh_activity.cli.load_cache")
    @patch("gh_activity.cli.save_cache")
    def test_stale_cache_triggers_refetch(
        self, mock_save, mock_load, mock_search, mock_stats, mock_report
    ):
        """Cached data with date-only strings should trigger re-fetch."""
        mock_load.return_value = {
            "commits": [
                {"sha": "old", "repo": "r", "date": "2025-06-15", "message": "m"},
            ],
            "fetched_ranges": [["2025-06-01", "2025-06-30"]],
        }
        mock_search.return_value = [
            {"sha": "new", "repo": "r", "date": "2025-06-15T12:00:00Z", "message": "m"},
        ]

        main(["--username", "octocat", "--since", "2025-06-01", "--until", "2025-06-30"])

        # Stale cache invalidated → ranges cleared → gaps exist → fetch triggered
        mock_search.assert_called_once()
