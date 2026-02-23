#!/usr/bin/env python3
"""Generate sample chart PNGs from realistic mock commit data."""

import hashlib
import random
import sys
from datetime import date, timedelta
from pathlib import Path

# Add project root to path so we can import gh_activity
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gh_activity.report import aggregate, build_dataframe, make_commits_chart, make_lines_chart


REPOS = [
    "acme/web-app",
    "acme/api-server",
    "acme/shared-lib",
    "acme/infra",
    "acme/docs-site",
]

SINCE = date(2025, 6, 1)
UNTIL = date(2026, 2, 23)


def fake_sha(i: int) -> str:
    return hashlib.sha256(f"mock-{i}".encode()).hexdigest()[:12]


def generate_mock_commits() -> list[dict]:
    """Build ~9 months of realistic-looking commit data."""
    rng = random.Random(42)  # deterministic
    commits: list[dict] = []

    # Define some "sprint" weeks (busier) and "vacation" weeks (quiet)
    day = SINCE
    week_num = 0
    while day <= UNTIL:
        week_start = day
        # Vary weekly intensity
        if week_num in (4, 5, 12, 13, 22, 23, 30, 31):
            # Sprint weeks — high activity
            weekday_range = (5, 10)
            weekend_range = (0, 3)
        elif week_num in (8, 9, 26, 27):
            # Vacation / light weeks
            weekday_range = (0, 1)
            weekend_range = (0, 0)
        else:
            # Normal weeks
            weekday_range = (2, 6)
            weekend_range = (0, 2)

        for dow in range(7):
            if day > UNTIL:
                break
            is_weekend = dow >= 5
            lo, hi = weekend_range if is_weekend else weekday_range
            n_commits = rng.randint(lo, hi)

            for _ in range(n_commits):
                adds = rng.randint(5, 200)
                dels = rng.randint(2, min(80, adds))
                commits.append({
                    "sha": fake_sha(len(commits)),
                    "date": day.isoformat(),
                    "repo": rng.choice(REPOS),
                    "additions": adds,
                    "deletions": dels,
                })
            day += timedelta(days=1)
        week_num += 1

    return commits


def main() -> None:
    out_dir = Path(__file__).resolve().parent.parent / "docs"
    out_dir.mkdir(exist_ok=True)

    print(f"Generating mock data ({SINCE} to {UNTIL})...")
    commits = generate_mock_commits()
    print(f"  {len(commits)} commits across {len(REPOS)} repos")

    daily = build_dataframe(commits, SINCE, UNTIL)
    agg = aggregate(daily, "week")

    commits_fig = make_commits_chart(agg, "week")
    lines_fig = make_lines_chart(agg, "week")

    # Style tweaks for static export
    for fig in (commits_fig, lines_fig):
        fig.update_layout(
            width=900,
            template="plotly_white",
        )

    commits_path = out_dir / "commits-per-week.png"
    lines_path = out_dir / "lines-changed-per-week.png"

    print("Writing charts...")
    commits_fig.write_image(str(commits_path), scale=2)
    print(f"  {commits_path}")
    lines_fig.write_image(str(lines_path), scale=2)
    print(f"  {lines_path}")
    print("Done.")


if __name__ == "__main__":
    main()
