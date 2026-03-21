"""GitHub data fetching via gh CLI: REST search + GraphQL batch stats."""

import json
import subprocess
import sys
import time
from datetime import date, timedelta
from typing import Any


MAX_SEARCH_RESULTS = 300  # Keep low to avoid GitHub search API silently dropping results
GRAPHQL_BATCH_SIZE = 50
MAX_RETRIES = 5
INITIAL_BACKOFF = 30  # seconds


def _run_with_retry(cmd: list[str], label: str) -> subprocess.CompletedProcess:
    """Run a subprocess with exponential backoff on rate-limit errors."""
    backoff = INITIAL_BACKOFF
    for attempt in range(MAX_RETRIES):
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return result
        if "rate limit" in result.stderr.lower() and attempt < MAX_RETRIES - 1:
            print(f"  Rate limited, waiting {backoff}s before retry...", file=sys.stderr)
            time.sleep(backoff)
            backoff *= 2
            continue
        raise RuntimeError(f"{label} failed: {result.stderr.strip()}")
    raise RuntimeError(f"{label} failed after {MAX_RETRIES} retries")


def gh_api(endpoint: str, params: dict[str, str] | None = None) -> Any:
    """Call the GitHub REST API via gh CLI. Returns parsed JSON."""
    cmd = ["gh", "api", endpoint, "--header", "Accept: application/vnd.github.cloak-preview+json"]
    for k, v in (params or {}).items():
        cmd.extend(["-f", f"{k}={v}"])
    result = _run_with_retry(cmd, "gh api")
    return json.loads(result.stdout)


def gh_graphql(query: str) -> Any:
    """Call the GitHub GraphQL API via gh CLI. Returns parsed JSON."""
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    result = _run_with_retry(cmd, "gh graphql")
    return json.loads(result.stdout)


def get_authenticated_user() -> str:
    """Get the authenticated GitHub username."""
    result = subprocess.run(
        ["gh", "api", "user", "-q", ".login"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to get authenticated user: {result.stderr.strip()}")
    return result.stdout.strip()


def search_commits(
    username: str,
    since: date,
    until: date,
    progress_callback: Any = None,
) -> list[dict]:
    """Search for all commits by a user in a date range.

    Uses date-range chunking to handle >1000 results.
    Returns list of dicts with keys: sha, repo, date, message.
    """
    commits = []
    _search_commits_chunked(username, since, until, commits, progress_callback)
    # Deduplicate by SHA (overlapping ranges may produce duplicates)
    seen = set()
    unique = []
    for c in commits:
        if c["sha"] not in seen:
            seen.add(c["sha"])
            unique.append(c)
    return unique


def _search_commits_chunked(
    username: str,
    since: date,
    until: date,
    accumulator: list[dict],
    progress_callback: Any = None,
) -> None:
    """Recursive chunked search. Splits date range if results exceed MAX_SEARCH_RESULTS."""
    if since > until:
        return

    results, total_count = _fetch_search_page(username, since, until)
    if progress_callback:
        progress_callback(f"  Searching {since} to {until}: {total_count} commits found")

    if total_count <= MAX_SEARCH_RESULTS:
        accumulator.extend(results)
        # Paginate if needed (100 per page, up to 1000)
        fetched = len(results)
        page = 2
        while fetched < total_count and page <= 10:
            page_results, _ = _fetch_search_page(username, since, until, page=page)
            if not page_results:
                break
            accumulator.extend(page_results)
            fetched += len(page_results)
            page += 1
    else:
        # Split the date range in half and recurse
        mid = since + (until - since) // 2
        _search_commits_chunked(username, since, mid, accumulator, progress_callback)
        _search_commits_chunked(username, mid + timedelta(days=1), until, accumulator, progress_callback)


def _fetch_search_page(
    username: str,
    since: date,
    until: date,
    page: int = 1,
) -> tuple[list[dict], int]:
    """Fetch one page of commit search results. Returns (commits, total_count)."""
    query = f"author:{username}+author-date:{since.isoformat()}..{until.isoformat()}"
    endpoint = f"/search/commits?q={query}&per_page=100&page={page}&sort=committer-date&order=desc"
    data = gh_api(endpoint)
    total_count = data.get("total_count", 0)
    commits = []
    for item in data.get("items", []):
        repo_name = item.get("repository", {}).get("full_name", "unknown")
        commit_data = item.get("commit", {})
        author_date = commit_data.get("author", {}).get("date", "")
        # Use author date (when work was done) not committer date (when merged/rebased)
        commit_date = author_date if author_date else ""
        commits.append({
            "sha": item.get("sha", ""),
            "repo": repo_name,
            "date": commit_date,
            "message": commit_data.get("message", "").split("\n")[0][:120],
        })
    return commits, total_count


def fetch_commit_stats(commits: list[dict], progress_callback: Any = None) -> list[dict]:
    """Fetch additions/deletions for commits using GraphQL batch queries.

    Groups commits by repo, then batches up to GRAPHQL_BATCH_SIZE per query.
    Returns commits with added 'additions' and 'deletions' keys.
    """
    if not commits:
        return commits

    # Group by repo
    by_repo: dict[str, list[dict]] = {}
    for c in commits:
        by_repo.setdefault(c["repo"], []).append(c)

    # Build batches: each batch contains commits from one repo, up to GRAPHQL_BATCH_SIZE
    batches: list[tuple[str, list[dict]]] = []
    for repo, repo_commits in by_repo.items():
        for i in range(0, len(repo_commits), GRAPHQL_BATCH_SIZE):
            batches.append((repo, repo_commits[i:i + GRAPHQL_BATCH_SIZE]))

    stats_map: dict[str, dict[str, int]] = {}  # sha -> {additions, deletions}
    for batch_idx, (repo, batch) in enumerate(batches):
        if progress_callback:
            progress_callback(f"  Fetching stats batch {batch_idx + 1}/{len(batches)} ({repo})")
        try:
            batch_stats = _fetch_graphql_batch(repo, batch)
            stats_map.update(batch_stats)
        except RuntimeError:
            # If GraphQL fails for a repo, fill with zeros
            if progress_callback:
                progress_callback(f"  Warning: could not fetch stats for {repo}")
            for c in batch:
                stats_map[c["sha"]] = {"additions": 0, "deletions": 0}

    # Merge stats into commits
    for c in commits:
        s = stats_map.get(c["sha"], {"additions": 0, "deletions": 0})
        c["additions"] = s["additions"]
        c["deletions"] = s["deletions"]
    return commits


def _fetch_graphql_batch(repo: str, commits: list[dict]) -> dict[str, dict[str, int]]:
    """Fetch additions/deletions for a batch of commits in one repo via GraphQL."""
    owner, name = repo.split("/", 1)
    fragments = []
    for i, c in enumerate(commits):
        sha = c["sha"]
        fragments.append(f"""
    c{i}: object(oid: "{sha}") {{
      ... on Commit {{
        oid
        additions
        deletions
      }}
    }}""")

    query = f"""{{
  repository(owner: "{owner}", name: "{name}") {{{chr(10).join(fragments)}
  }}
}}"""

    data = gh_graphql(query)
    repo_data = data.get("data", {}).get("repository", {})
    result = {}
    for i, c in enumerate(commits):
        node = repo_data.get(f"c{i}")
        if node:
            result[c["sha"]] = {
                "additions": node.get("additions", 0),
                "deletions": node.get("deletions", 0),
            }
        else:
            result[c["sha"]] = {"additions": 0, "deletions": 0}
    return result
