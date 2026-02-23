"""Tests for gh_activity.fetch."""

from datetime import date
from unittest.mock import patch, MagicMock

from gh_activity.fetch import (
    search_commits,
    _fetch_graphql_batch,
    _fetch_search_page,
    GRAPHQL_BATCH_SIZE,
)


class TestFetchSearchPage:
    @patch("gh_activity.fetch.gh_api")
    def test_parses_search_results(self, mock_api):
        mock_api.return_value = {
            "total_count": 2,
            "items": [
                {
                    "sha": "abc123",
                    "repository": {"full_name": "user/repo1"},
                    "commit": {
                        "committer": {"date": "2025-06-15T10:00:00Z"},
                        "message": "Fix bug",
                    },
                },
                {
                    "sha": "def456",
                    "repository": {"full_name": "user/repo2"},
                    "commit": {
                        "committer": {"date": "2025-06-16T12:00:00Z"},
                        "message": "Add feature\n\nLong description",
                    },
                },
            ],
        }
        commits, total = _fetch_search_page("user", date(2025, 6, 1), date(2025, 6, 30))
        assert total == 2
        assert len(commits) == 2
        assert commits[0]["sha"] == "abc123"
        assert commits[0]["repo"] == "user/repo1"
        assert commits[0]["date"] == "2025-06-15"
        assert commits[1]["message"] == "Add feature"  # first line only

    @patch("gh_activity.fetch.gh_api")
    def test_empty_results(self, mock_api):
        mock_api.return_value = {"total_count": 0, "items": []}
        commits, total = _fetch_search_page("user", date(2025, 6, 1), date(2025, 6, 30))
        assert total == 0
        assert commits == []


class TestSearchCommits:
    @patch("gh_activity.fetch._fetch_search_page")
    def test_deduplicates(self, mock_fetch):
        mock_fetch.return_value = (
            [
                {"sha": "aaa", "repo": "r", "date": "2025-01-01", "message": "m"},
                {"sha": "aaa", "repo": "r", "date": "2025-01-01", "message": "m"},
            ],
            2,
        )
        result = search_commits("user", date(2025, 1, 1), date(2025, 1, 31))
        assert len(result) == 1

    @patch("gh_activity.fetch._fetch_search_page")
    def test_chunks_when_over_limit(self, mock_fetch):
        # First call: too many results, triggers chunking
        # Second call (first half): 500 results
        # Third call (second half): 400 results
        call_count = 0

        def side_effect(username, since, until, page=1):
            nonlocal call_count
            call_count += 1
            if since == date(2025, 1, 1) and until == date(2025, 12, 31):
                return ([], 1500)  # Over limit, triggers split
            elif until <= date(2025, 7, 2):
                return (
                    [{"sha": f"a{i}", "repo": "r", "date": "2025-03-01", "message": "m"}
                     for i in range(3)],
                    3,
                )
            else:
                return (
                    [{"sha": f"b{i}", "repo": "r", "date": "2025-09-01", "message": "m"}
                     for i in range(2)],
                    2,
                )

        mock_fetch.side_effect = side_effect
        result = search_commits("user", date(2025, 1, 1), date(2025, 12, 31))
        assert len(result) == 5


class TestGraphQLBatch:
    @patch("gh_activity.fetch.gh_graphql")
    def test_parses_graphql_response(self, mock_gql):
        commits = [
            {"sha": "abc123", "repo": "user/repo"},
            {"sha": "def456", "repo": "user/repo"},
        ]
        mock_gql.return_value = {
            "data": {
                "repository": {
                    "c0": {"oid": "abc123", "additions": 10, "deletions": 5},
                    "c1": {"oid": "def456", "additions": 20, "deletions": 3},
                }
            }
        }
        result = _fetch_graphql_batch("user/repo", commits)
        assert result["abc123"] == {"additions": 10, "deletions": 5}
        assert result["def456"] == {"additions": 20, "deletions": 3}

    @patch("gh_activity.fetch.gh_graphql")
    def test_handles_missing_node(self, mock_gql):
        commits = [{"sha": "abc123", "repo": "user/repo"}]
        mock_gql.return_value = {
            "data": {
                "repository": {
                    "c0": None,  # e.g. force-pushed commit
                }
            }
        }
        result = _fetch_graphql_batch("user/repo", commits)
        assert result["abc123"] == {"additions": 0, "deletions": 0}
