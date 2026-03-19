"""CLI entry point for gh-activity."""

import argparse
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from gh_activity.cache import (
    add_fetched_range,
    compute_gaps,
    invalidate_stale_timestamps,
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
        default=date.today() - timedelta(days=182),
        help="Start date in YYYY-MM-DD format (default: 6 months ago)",
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
        default=None,
        help="Aggregation granularity (default: auto based on date range)",
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
    parser.add_argument(
        "--timezone",
        default=None,
        help="IANA timezone for report (e.g. America/Los_Angeles). Default: system local timezone.",
    )
    parser.add_argument(
        "--cdn",
        action="store_true",
        help="Use Plotly CDN instead of embedding (smaller file, requires internet to view)",
    )
    return parser.parse_args(argv)


def resolve_granularity(since: date, until: date) -> str:
    """Auto-select granularity based on date range span."""
    span = (until - since).days
    if span < 30:
        return "day"
    elif span <= 180:
        return "week"
    else:
        return "month"


def resolve_timezone(tz_arg: str | None):
    """Resolve timezone from CLI arg or system default."""
    if tz_arg:
        try:
            return ZoneInfo(tz_arg)
        except (KeyError, Exception):
            print(f"Error: Unknown timezone '{tz_arg}'", file=sys.stderr)
            sys.exit(1)
    # Use system local timezone
    return datetime.now().astimezone().tzinfo


def filter_commits_by_date(commits, since, until, tz):
    """Filter commits to the requested date range, timezone-aware."""
    filtered = []
    for c in commits:
        raw = c.get("date", "")
        if not raw:
            continue
        try:
            if len(raw) <= 10:
                commit_date = date.fromisoformat(raw)
            else:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                commit_date = dt.astimezone(tz).date()
        except (ValueError, TypeError):
            continue
        if since <= commit_date <= until:
            filtered.append(c)
    return filtered


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    # Resolve granularity
    user_specified_granularity = args.granularity is not None
    if not user_specified_granularity:
        args.granularity = resolve_granularity(args.since, args.until)
        progress(f"Auto-selected granularity: {args.granularity}")

    # Resolve timezone
    tz = resolve_timezone(args.timezone)
    progress(f"Timezone: {tz}")

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

        # Migrate stale cache entries (date-only → full timestamps)
        cache_data = invalidate_stale_timestamps(cache_data)
        new_count = len(cache_data.get("commits", []))
        if new_count < cached_count:
            progress(f"Cache: {cached_count - new_count} stale entries invalidated, will re-fetch")

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

    # Fetch line stats only for commits not already cached
    if all_new_commits:
        cached_shas = {c["sha"] for c in cache_data.get("commits", [])
                       if c.get("additions") is not None}
        truly_new = [c for c in all_new_commits if c["sha"] not in cached_shas]
        if truly_new:
            progress(f"Fetching line stats for {len(truly_new)} new commits "
                     f"({len(all_new_commits) - len(truly_new)} already cached)...")
            fetch_commit_stats(truly_new, progress_callback=progress)
        else:
            progress(f"Line stats already cached for all {len(all_new_commits)} commits")

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

    # Filter commits to requested date range (timezone-aware)
    commits = filter_commits_by_date(cache_data["commits"], args.since, args.until, tz)
    progress(f"Commits in range: {len(commits)}")

    # Generate report
    progress(f"Generating report: {args.output}")
    generate_report(
        commits=commits,
        since=args.since,
        until=args.until,
        granularity=args.granularity if user_specified_granularity else "auto",
        output_path=args.output,
        username=username,
        tz=tz,
        use_cdn=args.cdn,
        all_cached_commits=cache_data["commits"],
    )
    progress(f"Report written to {args.output}")


if __name__ == "__main__":
    main()
