# gh-activity

Fetch your GitHub commit history and generate an interactive HTML report with charts and metrics.

## Sample output

![Commits per week](docs/commits-per-week.png)

![Lines changed per week](docs/lines-changed-per-week.png)

## Install

Requires Python 3.10+ and the [GitHub CLI](https://cli.github.com/) (`gh`), authenticated.

```bash
pip install -e .
```

## Usage

```bash
# Default: last year of activity, weekly granularity
gh-activity

# Custom date range
gh-activity --since 2025-06-01 --until 2026-02-23

# Monthly granularity, specific output file
gh-activity --granularity month --output report.html

# Force re-fetch (ignore cache)
gh-activity --refresh

# Explicit username
gh-activity --username octocat
```

The report is written as a self-contained HTML file (default: `gh-activity-report.html`).

## What it does

1. **Fetches commits** via the GitHub search API using the `gh` CLI for authentication
2. **Retrieves line stats** (additions/deletions) via GitHub's GraphQL API in batches
3. **Caches results** locally (`~/.cache/gh-activity/`) so repeated runs only fetch new data
4. **Generates a report** with:
   - Summary metric cards (commits, lines added/deleted, net lines, active-day %)
   - Contribution calendar heatmap
   - Commits bar chart
   - Lines changed area chart (additions, deletions, net)
   - Weekly active-day percentage chart

## Development

```bash
pip install -e .
python -m pytest tests/
```
