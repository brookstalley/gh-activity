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
# Default: last year of activity, auto granularity
gh-activity

# Custom date range
gh-activity --since 2025-06-01 --until 2026-02-23

# Monthly granularity, specific output file
gh-activity --granularity month --output report.html

# Force re-fetch (ignore cache)
gh-activity --refresh

# Explicit username and timezone
gh-activity --username octocat --timezone America/Los_Angeles

# Use Plotly CDN instead of embedding (smaller file, requires internet to view)
gh-activity --cdn
```

The report is written as a self-contained HTML file (default: `gh-activity-report.html`). Granularity auto-selects based on date range: day (<30 days), week (30–180 days), or month (>180 days).

## What it does

1. **Fetches commits** via the GitHub search API using the `gh` CLI for authentication, searching by **author date** (when work was done, not when it was merged/rebased)
2. **Retrieves line stats** (additions/deletions) via GitHub's GraphQL API in batches
3. **Caches results** locally (`~/.cache/gh-activity/`) so repeated runs only fetch new data. Rate-limited API calls are retried automatically with exponential backoff.
4. **Generates an interactive report** — a self-contained HTML file with client-side rendering. All commit data is embedded as JSON, and controls let you adjust the date range, granularity, and timezone without re-fetching:
   - Summary metric cards with period-over-period comparison
   - Contribution calendar heatmap
   - Lines changed bar chart (additions, deletions, net)
   - Commits bar chart with rolling average
   - Day-of-week and hour-of-day distribution charts
   - Per-repository breakdown
   - Active-day percentage chart
   - Top commits by lines changed

## Development

```bash
pip install -e .
python -m pytest tests/
```
