"""CLI entry point for gh-activity."""

import argparse
import sys
from datetime import date, timedelta

from gh_activity.cache import (
    add_fetched_range,
    compute_gaps,
    load_cache,
    merge_commits,
    save_cache,
)
from gh_activity.fetch import fetch_commit_stats, get_authenticated_user, search_commits
from gh_activity.report import generate_report


def progress(msg: str) -> None:
    print(msg, file=sys.stderr)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gh-activity",
        description="Fetch GitHub commit activity and generate an interactive HTML report.",
    )
    parser.add_argument(
        "--since",
        type=date.fromisoformat,
        default=date.today() - timedelta(days=365),
        help="Start date in YYYY-MM-DD format (default: 1 year ago)",
    )
    parser.add_argument(
        "--until",
        type=date.fromisoformat,
        default=date.today(),
        help="End date in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--granularity",
        choices=["day", "week", "month"],
        default="week",
        help="Aggregation granularity (default: week)",
    )
    parser.add_argument(
        "--output",
        default="gh-activity-report.html",
        help="Output HTML file path (default: gh-activity-report.html)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force re-fetch all data (ignore cache)",
    )
    parser.add_argument(
        "--username",
        default=None,
        help="GitHub username (default: authenticated user)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    # Resolve username
    if args.username:
        username = args.username
    else:
        progress("Detecting GitHub username...")
        username = get_authenticated_user()
    progress(f"User: {username}")

    # Load cache
    if args.refresh:
        cache_data = {"commits": [], "fetched_ranges": []}
        progress("Cache: --refresh specified, starting fresh")
    else:
        cache_data = load_cache(username)
        cached_count = len(cache_data.get("commits", []))
        progress(f"Cache: {cached_count} commits loaded")

    # Determine what date ranges need fetching
    gaps = compute_gaps(
        cache_data.get("fetched_ranges", []),
        args.since,
        args.until,
    )

    if not gaps:
        progress("All requested data is cached.")
    else:
        progress(f"Fetching {len(gaps)} date range(s)...")

    # Fetch missing ranges
    all_new_commits: list[dict] = []
    for gap_start, gap_end in gaps:
        progress(f"Searching commits: {gap_start} to {gap_end}")
        new_commits = search_commits(username, gap_start, gap_end, progress_callback=progress)
        progress(f"  Found {len(new_commits)} commits")
        all_new_commits.extend(new_commits)

    # Fetch line stats for new commits
    if all_new_commits:
        progress(f"Fetching line stats for {len(all_new_commits)} commits...")
        fetch_commit_stats(all_new_commits, progress_callback=progress)

    # Merge into cache
    cache_data["commits"] = merge_commits(
        cache_data.get("commits", []),
        all_new_commits,
    )
    for gap_start, gap_end in gaps:
        cache_data["fetched_ranges"] = add_fetched_range(
            cache_data.get("fetched_ranges", []),
            gap_start.isoformat(),
            gap_end.isoformat(),
        )

    # Save cache
    save_cache(username, cache_data)
    progress(f"Cache: {len(cache_data['commits'])} total commits saved")

    # Filter commits to requested date range
    commits = [
        c for c in cache_data["commits"]
        if args.since.isoformat() <= c.get("date", "") <= args.until.isoformat()
    ]
    progress(f"Commits in range: {len(commits)}")

    # Generate report
    progress(f"Generating report: {args.output}")
    generate_report(
        commits=commits,
        since=args.since,
        until=args.until,
        granularity=args.granularity,
        output_path=args.output,
        username=username,
    )
    progress(f"Report written to {args.output}")


if __name__ == "__main__":
    main()
