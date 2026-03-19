"""Report generation: data processing + HTML/JS interactive report assembly."""

from datetime import date, datetime, timedelta, timezone
from html import escape
import json

import pandas as pd
import plotly.offline


# ---------------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------------

def build_dataframe(
    commits: list[dict], since: date, until: date, tz=None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build daily and per-commit DataFrames from commit data.

    Returns (daily_df, commit_df).
    - daily_df: DatetimeIndex (date-level), columns: commits, additions, deletions
    - commit_df: one row per commit with timezone-converted 'date' column
    """
    if not commits:
        idx = pd.date_range(since, until, freq="D")
        daily = pd.DataFrame(
            {"commits": 0, "additions": 0, "deletions": 0},
            index=idx,
        )
        daily.index.name = "date"
        empty_commit_df = pd.DataFrame(
            columns=["sha", "repo", "date", "message", "additions", "deletions"],
        )
        return daily, empty_commit_df

    df = pd.DataFrame(commits)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    if tz is not None:
        df["date"] = df["date"].dt.tz_convert(tz)
    for col in ("additions", "deletions"):
        if col not in df.columns:
            df[col] = 0
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    # Preserve per-commit data for hour-of-day chart
    commit_df = df.copy()

    # Group by local date for daily aggregation
    df["local_date"] = df["date"].dt.normalize()
    daily = df.groupby("local_date").agg(
        commits=("sha", "count"),
        additions=("additions", "sum"),
        deletions=("deletions", "sum"),
    )

    # Reindex to full date range
    if tz is not None:
        idx = pd.date_range(since, until, freq="D", tz=tz)
    else:
        idx = pd.date_range(since, until, freq="D", tz="UTC")
    daily = daily.reindex(idx, fill_value=0)
    daily.index.name = "date"
    return daily, commit_df


def aggregate(daily: pd.DataFrame, granularity: str) -> pd.DataFrame:
    """Aggregate daily data to week or month granularity."""
    if granularity == "day":
        return daily
    freq = "W-MON" if granularity == "week" else "MS"
    return daily.resample(freq).sum()


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _compute_streak_stats(daily: pd.DataFrame) -> dict:
    """Compute streak and gap statistics from daily commit data."""
    active = daily["commits"] > 0
    active_dates = daily.index[active]

    if len(active_dates) == 0:
        return {"current_streak": 0, "longest_streak": 0, "avg_gap": 0.0}

    # Longest streak: group consecutive active days
    groups = (active != active.shift()).cumsum()
    active_only = active[active]
    if active_only.empty:
        longest = 0
    else:
        longest = int(active_only.groupby(groups).size().max())

    # Current streak: count backwards from end of range
    current = 0
    for val in reversed(active.values):
        if val:
            current += 1
        else:
            break

    # Average gap between active days
    if len(active_dates) >= 2:
        gaps = pd.Series(active_dates).diff().dt.days.dropna()
        avg_gap = round(float(gaps.mean()), 1)
    else:
        avg_gap = 0.0

    return {
        "current_streak": current,
        "longest_streak": longest,
        "avg_gap": avg_gap,
    }


def compute_metrics(daily: pd.DataFrame, since: date, until: date) -> dict:
    """Compute summary metrics from daily data."""
    total_days = (until - since).days + 1
    active_days = int((daily["commits"] > 0).sum())
    streak = _compute_streak_stats(daily)
    return {
        "total_commits": int(daily["commits"].sum()),
        "total_additions": int(daily["additions"].sum()),
        "total_deletions": int(daily["deletions"].sum()),
        "net_lines": int(daily["additions"].sum() - daily["deletions"].sum()),
        "active_days": active_days,
        "total_days": total_days,
        "active_day_pct": round(active_days / total_days * 100, 1) if total_days > 0 else 0,
        **streak,
    }


def compute_period_comparison(
    all_commits: list[dict], since: date, until: date, tz=None,
) -> dict | None:
    """Compute metric deltas vs the equivalent prior period.

    Returns dict of deltas or None if prior period data is unavailable.
    """
    duration = (until - since).days + 1
    prior_until = since - timedelta(days=1)
    prior_since = prior_until - timedelta(days=duration - 1)

    prior_commits = _filter_commits_by_date(all_commits, prior_since, prior_until, tz)
    if not prior_commits:
        return None

    prior_daily, _ = build_dataframe(prior_commits, prior_since, prior_until, tz)
    prior = compute_metrics(prior_daily, prior_since, prior_until)

    return prior


def _filter_commits_by_date(commits, since, until, tz=None):
    """Filter commits by date range with timezone awareness."""
    filtered = []
    for c in commits:
        raw = c.get("date", "")
        if not raw:
            continue
        try:
            if len(raw) <= 10:
                # Legacy date-only format
                commit_date = date.fromisoformat(raw)
            else:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if tz is not None:
                    commit_date = dt.astimezone(tz).date()
                else:
                    commit_date = dt.date()
        except (ValueError, TypeError):
            continue
        if since <= commit_date <= until:
            filtered.append(c)
    return filtered


# ---------------------------------------------------------------------------
# Partial period helpers (kept for test validation of JS logic)
# ---------------------------------------------------------------------------

def _partial_period_info(
    last_label: pd.Timestamp, granularity: str, until: date,
) -> tuple[bool, int, int]:
    """Check if the last aggregation period is partial.

    Returns (is_partial, actual_days, expected_days).
    """
    until_ts = pd.Timestamp(until)

    if granularity == "week":
        period_end = last_label.tz_localize(None) if last_label.tz else last_label
        period_start = period_end - pd.Timedelta(days=6)
        expected_days = 7
    elif granularity == "month":
        period_start = last_label.tz_localize(None) if last_label.tz else last_label
        if period_start.month == 12:
            next_month = pd.Timestamp(year=period_start.year + 1, month=1, day=1)
        else:
            next_month = pd.Timestamp(
                year=period_start.year, month=period_start.month + 1, day=1,
            )
        period_end = next_month - pd.Timedelta(days=1)
        expected_days = (period_end - period_start).days + 1
    else:
        return False, 0, 0

    if until_ts < period_end:
        actual_days = (until_ts - period_start).days + 1
        return True, max(actual_days, 1), expected_days
    return False, 0, 0


def _first_partial_period_info(
    first_label: pd.Timestamp, granularity: str, since: date,
) -> tuple[bool, int, int]:
    """Check if the first aggregation period is partial.

    Returns (is_partial, actual_days, expected_days).
    """
    since_ts = pd.Timestamp(since)

    if granularity == "week":
        period_end = first_label.tz_localize(None) if first_label.tz else first_label
        period_start = period_end - pd.Timedelta(days=6)
        expected_days = 7
    elif granularity == "month":
        period_start = first_label.tz_localize(None) if first_label.tz else first_label
        if period_start.month == 12:
            next_month = pd.Timestamp(year=period_start.year + 1, month=1, day=1)
        else:
            next_month = pd.Timestamp(
                year=period_start.year, month=period_start.month + 1, day=1,
            )
        period_end = next_month - pd.Timedelta(days=1)
        expected_days = (period_end - period_start).days + 1
    else:
        return False, 0, 0

    if since_ts > period_start:
        actual_days = (period_end - since_ts).days + 1
        return True, max(actual_days, 1), expected_days
    return False, 0, 0


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
  :root {
    --bg: #f6f8fa;
    --fg: #24292e;
    --card-bg: white;
    --border: #e1e4e8;
    --muted: #586069;
    --link: #0366d6;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0d1117;
      --fg: #c9d1d9;
      --card-bg: #161b22;
      --border: #30363d;
      --muted: #8b949e;
      --link: #58a6ff;
    }
  }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    max-width: 1200px;
    margin: 0 auto;
    padding: 20px;
    background: var(--bg);
    color: var(--fg);
  }
  h1 { border-bottom: 1px solid var(--border); padding-bottom: 10px; }
  h3 { color: var(--fg); }
  .controls {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: center;
    padding: 12px 16px;
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    margin: 16px 0;
    font-size: 0.9em;
  }
  .controls label {
    display: flex;
    align-items: center;
    gap: 4px;
    color: var(--muted);
  }
  .controls input[type="date"],
  .controls select {
    padding: 4px 8px;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--bg);
    color: var(--fg);
    font-size: 0.9em;
  }
  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 16px;
    margin: 20px 0;
  }
  .card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 16px;
    text-align: center;
  }
  .card .value {
    font-size: 2em;
    font-weight: 600;
    color: var(--link);
  }
  .card .label {
    font-size: 0.9em;
    color: var(--muted);
    margin-top: 4px;
  }
  .card .delta {
    font-size: 0.8em;
    font-weight: 600;
    margin-top: 2px;
  }
  .chart-container {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px;
    margin: 16px 0;
  }
  .top-commits {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9em;
  }
  .top-commits th, .top-commits td {
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    text-align: left;
  }
  .top-commits th {
    background: var(--bg);
    color: var(--fg);
    font-weight: 600;
  }
  .top-commits td { color: var(--fg); }
  .top-commits td.commit-msg {
    max-width: 400px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .top-commits tr:hover td {
    background: var(--bg);
  }
  .chart-section { margin: 16px 0; }
  .chart-section h3 {
    margin: 0 0 8px 4px;
    font-size: 1.05em;
    font-weight: 600;
  }
  .chart-row {
    display: flex;
    gap: 16px;
    margin: 16px 0;
  }
  .chart-half {
    flex: 1;
    min-width: 0;
  }
  @media (max-width: 768px) {
    .chart-row { flex-direction: column; }
  }
  .footer {
    text-align: center;
    color: var(--muted);
    font-size: 0.85em;
    margin-top: 30px;
    padding-top: 10px;
    border-top: 1px solid var(--border);
  }
"""


# ---------------------------------------------------------------------------
# JavaScript application
# ---------------------------------------------------------------------------

_JS_APP = r"""
(function() {
'use strict';

// ── Section 1: Data Layer ──────────────────────────────────────────────

var STATE = {};

function loadData() {
  STATE.allCommits = JSON.parse(document.getElementById('commit-data').textContent);
  var init = JSON.parse(document.getElementById('initial-state').textContent);
  STATE.username = init.username;
  document.getElementById('ctrl-since').value = init.since;
  document.getElementById('ctrl-until').value = init.until;
  document.getElementById('ctrl-granularity').value = init.granularity;
  var tz = init.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
  populateTimezones(tz);
}

function parseDate(s) { return new Date(s + 'T12:00:00'); }

function daysBetween(a, b) {
  return Math.round((parseDate(b) - parseDate(a)) / 86400000);
}

function addDays(dateStr, n) {
  var d = parseDate(dateStr);
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

function daysInMonth(dateStr) {
  var d = parseDate(dateStr);
  return new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
}

function utcToLocalDate(iso, tz) {
  return new Date(iso).toLocaleDateString('en-CA', {timeZone: tz});
}

function utcToLocalHour(iso, tz) {
  return parseInt(new Date(iso).toLocaleTimeString('en-GB', {timeZone: tz, hour12: false}), 10);
}

function filterCommits(commits, since, until, tz) {
  return commits.filter(function(c) {
    if (!c.date) return false;
    var d = utcToLocalDate(c.date, tz);
    return d >= since && d <= until;
  });
}

function buildDaily(commits, since, until, tz) {
  var byDate = {};
  commits.forEach(function(c) {
    var d = utcToLocalDate(c.date, tz);
    if (!byDate[d]) byDate[d] = {date: d, commits: 0, additions: 0, deletions: 0};
    byDate[d].commits++;
    byDate[d].additions += (c.additions || 0);
    byDate[d].deletions += (c.deletions || 0);
  });
  var result = [];
  var total = daysBetween(since, until) + 1;
  var cur = since;
  for (var i = 0; i < total; i++) {
    result.push(byDate[cur] || {date: cur, commits: 0, additions: 0, deletions: 0});
    cur = addDays(cur, 1);
  }
  return result;
}

function getMondayOfWeek(dateStr) {
  var d = parseDate(dateStr);
  var day = d.getDay();
  var diff = day === 0 ? -6 : 1 - day;
  d.setDate(d.getDate() + diff);
  return d.toISOString().slice(0, 10);
}

function aggregateData(daily, granularity) {
  if (granularity === 'day') return daily;
  var groups = {};
  daily.forEach(function(d) {
    var key = granularity === 'week' ? getMondayOfWeek(d.date) : d.date.slice(0, 7) + '-01';
    if (!groups[key]) groups[key] = {date: key, commits: 0, additions: 0, deletions: 0};
    groups[key].commits += d.commits;
    groups[key].additions += d.additions;
    groups[key].deletions += d.deletions;
  });
  return Object.keys(groups).sort().map(function(k) { return groups[k]; });
}

function resolveGranularity(since, until) {
  var span = daysBetween(since, until);
  if (span < 30) return 'day';
  if (span <= 180) return 'week';
  return 'month';
}

// ── Section 2: Metrics ─────────────────────────────────────────────────

function computeMetrics(daily, since, until) {
  var totalDays = daysBetween(since, until) + 1;
  var totalCommits = 0, totalAdditions = 0, totalDeletions = 0, activeDays = 0;
  daily.forEach(function(d) {
    totalCommits += d.commits;
    totalAdditions += d.additions;
    totalDeletions += d.deletions;
    if (d.commits > 0) activeDays++;
  });
  var longestStreak = 0, streak = 0, currentStreak = 0;
  for (var i = 0; i < daily.length; i++) {
    if (daily[i].commits > 0) { streak++; longestStreak = Math.max(longestStreak, streak); }
    else streak = 0;
  }
  for (var j = daily.length - 1; j >= 0; j--) {
    if (daily[j].commits > 0) currentStreak++;
    else break;
  }
  var avgGap = 0, activeIdx = [];
  daily.forEach(function(d, i) { if (d.commits > 0) activeIdx.push(i); });
  if (activeIdx.length >= 2) {
    var gapSum = 0;
    for (var k = 1; k < activeIdx.length; k++) gapSum += activeIdx[k] - activeIdx[k - 1];
    avgGap = Math.round(gapSum / (activeIdx.length - 1) * 10) / 10;
  }
  return {
    totalCommits: totalCommits, totalAdditions: totalAdditions,
    totalDeletions: totalDeletions, netLines: totalAdditions - totalDeletions,
    activeDays: activeDays, totalDays: totalDays,
    activeDayPct: totalDays > 0 ? Math.round(activeDays / totalDays * 1000) / 10 : 0,
    currentStreak: currentStreak, longestStreak: longestStreak, avgGap: avgGap
  };
}

function computePeriodComparison(allCommits, since, until, tz) {
  var duration = daysBetween(since, until) + 1;
  var priorUntil = addDays(since, -1);
  var priorSince = addDays(priorUntil, -(duration - 1));
  var priorCommits = filterCommits(allCommits, priorSince, priorUntil, tz);
  if (priorCommits.length === 0) return null;
  var priorDaily = buildDaily(priorCommits, priorSince, priorUntil, tz);
  return computeMetrics(priorDaily, priorSince, priorUntil);
}

function computeRollingAvg(values, window) {
  var result = [], half = Math.floor(window / 2);
  for (var i = 0; i < values.length; i++) {
    var sum = 0, count = 0;
    for (var j = Math.max(0, i - half); j <= Math.min(values.length - 1, i + half); j++) {
      sum += values[j]; count++;
    }
    result.push(sum / count);
  }
  return result;
}

// ── Section 3: Chart Renderers ─────────────────────────────────────────

function themeLayout() {
  var dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  var fg = dark ? '#c9d1d9' : '#24292e';
  var grid = dark ? '#30363d' : '#e1e4e8';
  return {
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
    font: {color: fg},
    xaxis: {gridcolor: grid, linecolor: grid},
    yaxis: {gridcolor: grid, linecolor: grid}
  };
}

function renderHeatmap(daily) {
  var section = document.getElementById('heatmap-section');
  var container = document.getElementById('heatmap-container');
  container.innerHTML = '';
  if (daily.length === 0) { section.style.display = 'none'; return; }
  section.style.display = '';

  var years = {};
  daily.forEach(function(d) {
    var y = d.date.slice(0, 4);
    if (!years[y]) years[y] = [];
    years[y].push(d);
  });
  var yearKeys = Object.keys(years).sort();
  var totalWeeks = Math.ceil(daily.length / 7);

  var dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  var cs = dark
    ? [[0,'#161b22'],[0.01,'#0e4429'],[0.33,'#006d32'],[0.66,'#26a641'],[1.0,'#39d353']]
    : [[0,'#ebedf0'],[0.01,'#9be9a8'],[0.33,'#40c463'],[0.66,'#30a14e'],[1.0,'#216e39']];
  var theme = themeLayout();
  var multiYear = totalWeeks > 53 && yearKeys.length > 1;

  function renderYear(yearData, title, parentEl) {
    var div = document.createElement('div');
    parentEl.appendChild(div);
    var minDate = parseDate(yearData[0].date);
    var x = [], y = [], z = [], cd = [];
    yearData.forEach(function(d) {
      var dt = parseDate(d.date);
      x.push(Math.floor((dt - minDate) / (7 * 86400000)));
      y.push((dt.getDay() + 6) % 7);
      z.push(d.commits);
      cd.push([d.date]);
    });
    var layout = {
      paper_bgcolor: theme.paper_bgcolor, plot_bgcolor: theme.plot_bgcolor,
      font: theme.font,
      yaxis: {tickvals: [0,1,2,3,4,5,6],
              ticktext: ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'],
              autorange: 'reversed', gridcolor: theme.yaxis.gridcolor},
      xaxis: {showticklabels: false, gridcolor: theme.xaxis.gridcolor},
      height: multiYear ? 180 : 200,
      margin: {l: 50, r: 20, t: title ? 40 : 20, b: 20}
    };
    if (title) layout.title = title;
    Plotly.react(div, [{
      type: 'heatmap', x: x, y: y, z: z, customdata: cd, colorscale: cs,
      showscale: false, xgap: 3, ygap: 3,
      hovertemplate: '%{customdata[0]}<br>%{z} commits<extra></extra>'
    }], layout, {responsive: true});
  }

  if (multiYear) {
    yearKeys.forEach(function(year) { renderYear(years[year], year, container); });
  } else {
    renderYear(daily, null, container);
  }
}

function renderRepoBreakdown(commits) {
  var section = document.getElementById('repo-section');
  var el = document.getElementById('repo-breakdown');
  if (commits.length === 0) { section.style.display = 'none'; return; }
  section.style.display = '';

  var byRepo = {};
  commits.forEach(function(c) {
    var r = c.repo || 'unknown';
    if (!byRepo[r]) byRepo[r] = {commits: 0, additions: 0, deletions: 0};
    byRepo[r].commits++;
    byRepo[r].additions += (c.additions || 0);
    byRepo[r].deletions += (c.deletions || 0);
  });

  var repos = Object.keys(byRepo).map(function(r) {
    var s = byRepo[r];
    return {repo: r, commits: s.commits, additions: s.additions,
            deletions: s.deletions, total: s.additions + s.deletions};
  }).sort(function(a, b) { return b.total - a.total; });

  if (repos.length > 15) {
    var top = repos.slice(0, 15);
    var rest = repos.slice(15);
    var other = {repo: 'Other (' + rest.length + ' repos)', commits: 0,
                 additions: 0, deletions: 0, total: 0};
    rest.forEach(function(r) {
      other.commits += r.commits; other.additions += r.additions;
      other.deletions += r.deletions; other.total += r.total;
    });
    repos = top.concat([other]);
  }
  repos.sort(function(a, b) { return a.total - b.total; });

  var theme = themeLayout();
  Plotly.react(el, [{
    type: 'bar', orientation: 'h',
    y: repos.map(function(r) { return r.repo; }),
    x: repos.map(function(r) { return r.total; }),
    marker: {color: '#4078c0'},
    customdata: repos.map(function(r) { return [r.commits, r.additions, r.deletions]; }),
    hovertemplate: '<b>%{y}</b><br>%{customdata[0]} commits<br>' +
      '+%{customdata[1]:,} / -%{customdata[2]:,}<extra></extra>'
  }], {
    paper_bgcolor: theme.paper_bgcolor, plot_bgcolor: theme.plot_bgcolor,
    font: theme.font,
    xaxis: {title: 'Total Lines Changed', gridcolor: theme.xaxis.gridcolor,
            linecolor: theme.xaxis.linecolor},
    yaxis: {gridcolor: theme.yaxis.gridcolor, linecolor: theme.yaxis.linecolor},
    height: Math.max(300, 30 * repos.length),
    margin: {l: 200, r: 20, t: 20, b: 50}
  }, {responsive: true});
}

function renderDOW(daily) {
  var counts = [0,0,0,0,0,0,0];
  daily.forEach(function(d) {
    counts[(parseDate(d.date).getDay() + 6) % 7] += d.commits;
  });
  var theme = themeLayout();
  Plotly.react('dow-chart', [{
    type: 'bar',
    x: ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'], y: counts,
    marker: {color: ['#4078c0','#4078c0','#4078c0','#4078c0','#4078c0','#6f42c1','#6f42c1']},
    hovertemplate: '%{x}<br>%{y} commits<extra></extra>'
  }], {
    paper_bgcolor: theme.paper_bgcolor, plot_bgcolor: theme.plot_bgcolor,
    font: theme.font,
    xaxis: {title: 'Day', gridcolor: theme.xaxis.gridcolor, linecolor: theme.xaxis.linecolor},
    yaxis: {title: 'Commits', gridcolor: theme.yaxis.gridcolor, linecolor: theme.yaxis.linecolor},
    height: 300, margin: {l: 50, r: 20, t: 20, b: 50}
  }, {responsive: true});
}

function renderHourChart(commits, tz) {
  var counts = new Array(24).fill(0);
  commits.forEach(function(c) { if (c.date) counts[utcToLocalHour(c.date, tz)]++; });
  var hours = Array.from({length: 24}, function(_, i) { return i; });
  var theme = themeLayout();
  Plotly.react('hour-chart', [{
    type: 'bar', x: hours, y: counts, marker: {color: '#4078c0'},
    hovertemplate: '%{x}:00<br>%{y} commits<extra></extra>'
  }], {
    paper_bgcolor: theme.paper_bgcolor, plot_bgcolor: theme.plot_bgcolor,
    font: theme.font,
    xaxis: {title: 'Hour', gridcolor: theme.xaxis.gridcolor, linecolor: theme.xaxis.linecolor,
            tickvals: [0,3,6,9,12,15,18,21],
            ticktext: ['00:00','03:00','06:00','09:00','12:00','15:00','18:00','21:00']},
    yaxis: {title: 'Commits', gridcolor: theme.yaxis.gridcolor, linecolor: theme.yaxis.linecolor},
    height: 300, margin: {l: 50, r: 20, t: 20, b: 50}
  }, {responsive: true});
}

function renderCommitsChart(agg, gran) {
  var section = document.getElementById('commits-section');
  var capGran = gran.charAt(0).toUpperCase() + gran.slice(1);
  section.querySelector('h3').textContent = 'Commits per ' + capGran;

  var dates = agg.map(function(d) { return d.date; });
  var vals = agg.map(function(d) { return d.commits; });
  var traces = [{
    type: 'bar', x: dates, y: vals, marker: {color: '#4078c0'},
    hovertemplate: '%{x}<br>%{y} commits<extra></extra>'
  }];
  var wMap = {day: 7, week: 4, month: 3};
  var w = wMap[gran] || 4;
  if (vals.length >= 6) {
    traces.push({
      type: 'scatter', mode: 'lines', x: dates,
      y: computeRollingAvg(vals, w),
      name: w + '-' + gran + ' avg',
      line: {color: '#e36209', width: 2.5},
      hovertemplate: '%{x}<br>%{y:.1f} avg<extra></extra>'
    });
  }
  var theme = themeLayout();
  Plotly.react('commits-chart', traces, {
    paper_bgcolor: theme.paper_bgcolor, plot_bgcolor: theme.plot_bgcolor,
    font: theme.font,
    xaxis: {gridcolor: theme.xaxis.gridcolor, linecolor: theme.xaxis.linecolor},
    yaxis: {title: 'Commits', gridcolor: theme.yaxis.gridcolor, linecolor: theme.yaxis.linecolor},
    height: 350, margin: {l: 50, r: 20, t: 20, b: 50}
  }, {responsive: true});
}

function renderLinesChart(agg, gran) {
  var section = document.getElementById('lines-section');
  var el = document.getElementById('lines-chart');
  if (agg.length === 0) { section.style.display = 'none'; return; }
  section.style.display = '';
  var capGran = gran.charAt(0).toUpperCase() + gran.slice(1);
  section.querySelector('h3').textContent = 'Lines Changed per ' + capGran;

  var dates = agg.map(function(d) { return d.date; });
  var adds = agg.map(function(d) { return d.additions; });
  var dels = agg.map(function(d) { return -d.deletions; });
  var net = agg.map(function(d) { return d.additions - d.deletions; });

  var traces = [
    {type: 'scatter', x: dates, y: adds, name: 'Additions',
     fill: 'tozeroy', line: {color: '#2ea44f'}, fillcolor: 'rgba(46,164,79,0.3)',
     hovertemplate: '%{x}<br>+%{y:,} lines<extra></extra>'},
    {type: 'scatter', x: dates, y: dels, name: 'Deletions',
     fill: 'tozeroy', line: {color: '#d73a49'}, fillcolor: 'rgba(215,58,73,0.3)',
     hovertemplate: '%{x}<br>%{y:,} lines<extra></extra>'},
    {type: 'scatter', mode: 'lines', x: dates, y: net, name: 'Net',
     line: {color: '#0366d6', width: 2},
     hovertemplate: '%{x}<br>Net: %{y:,} lines<extra></extra>'}
  ];

  var theme = themeLayout();
  Plotly.react(el, traces, {
    paper_bgcolor: theme.paper_bgcolor, plot_bgcolor: theme.plot_bgcolor,
    font: theme.font,
    xaxis: {gridcolor: theme.xaxis.gridcolor, linecolor: theme.xaxis.linecolor},
    yaxis: {title: 'Lines', gridcolor: theme.yaxis.gridcolor, linecolor: theme.yaxis.linecolor},
    height: 350, margin: {l: 60, r: 20, t: 20, b: 50}
  }, {responsive: true});
}

function renderActiveDaysChart(daily, gran) {
  var section = document.getElementById('active-days-section');
  var el = document.getElementById('active-days-chart');
  if (gran === 'day') { section.style.display = 'none'; return; }
  section.style.display = '';
  var capGran = gran.charAt(0).toUpperCase() + gran.slice(1);
  section.querySelector('h3').textContent = 'Active-Day % by ' + capGran;

  var groups = {};
  daily.forEach(function(d) {
    var key = gran === 'week' ? getMondayOfWeek(d.date) : d.date.slice(0, 7) + '-01';
    if (!groups[key]) groups[key] = {active: 0, total: 0};
    groups[key].total++;
    if (d.commits > 0) groups[key].active++;
  });

  var keys = Object.keys(groups).sort();
  var pcts = keys.map(function(k) { return Math.round(groups[k].active / groups[k].total * 1000) / 10; });
  var colors = pcts.map(function(p) { return p < 30 ? '#d73a49' : p < 60 ? '#e36209' : '#2ea44f'; });
  var cd = keys.map(function(k) { return [groups[k].active, groups[k].total]; });

  var theme = themeLayout();
  Plotly.react(el, [{
    type: 'bar', x: keys, y: pcts, marker: {color: colors}, customdata: cd,
    hovertemplate: '%{x}<br>%{y:.1f}% active<br>%{customdata[0]}/%{customdata[1]} days<extra></extra>'
  }], {
    paper_bgcolor: theme.paper_bgcolor, plot_bgcolor: theme.plot_bgcolor,
    font: theme.font,
    xaxis: {title: capGran, gridcolor: theme.xaxis.gridcolor, linecolor: theme.xaxis.linecolor},
    yaxis: {title: '% of days active', range: [0, 100],
            gridcolor: theme.yaxis.gridcolor, linecolor: theme.yaxis.linecolor},
    height: 300, margin: {l: 50, r: 20, t: 20, b: 50}
  }, {responsive: true});
}

function renderTopCommits(commits) {
  var section = document.getElementById('top-commits-section');
  var container = document.getElementById('top-commits');
  container.innerHTML = '';
  var scored = commits.map(function(c) {
    return {date: c.date, repo: c.repo, message: c.message,
            additions: c.additions || 0, deletions: c.deletions || 0,
            total: (c.additions || 0) + (c.deletions || 0)};
  }).filter(function(c) { return c.total > 0; })
    .sort(function(a, b) { return b.total - a.total; })
    .slice(0, 10);

  if (scored.length === 0) { section.style.display = 'none'; return; }
  section.style.display = '';

  var table = document.createElement('table');
  table.className = 'top-commits';
  var thead = document.createElement('thead');
  var hr = document.createElement('tr');
  ['Date','Repo','Message','Added','Deleted','Total'].forEach(function(t) {
    var th = document.createElement('th');
    th.textContent = t;
    hr.appendChild(th);
  });
  thead.appendChild(hr);
  table.appendChild(thead);

  var tbody = document.createElement('tbody');
  scored.forEach(function(c) {
    var tr = document.createElement('tr');
    var vals = [
      c.date ? c.date.slice(0, 10) : '',
      c.repo || '',
      c.message || '',
      '+' + c.additions.toLocaleString(),
      '-' + c.deletions.toLocaleString(),
      c.total.toLocaleString()
    ];
    vals.forEach(function(text, i) {
      var td = document.createElement('td');
      if (i === 5) {
        var strong = document.createElement('strong');
        strong.textContent = text;
        td.appendChild(strong);
      } else {
        td.textContent = text;
      }
      if (i === 2) td.className = 'commit-msg';
      if (i === 3) td.style.color = '#2ea44f';
      if (i === 4) td.style.color = '#d73a49';
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  container.appendChild(table);
}

function renderCards(metrics, prior) {
  var container = document.getElementById('cards');
  container.innerHTML = '';

  function deltaHtml(key, priorM, invert) {
    if (!priorM) return '';
    var cur = metrics[key] || 0, prev = priorM[key] || 0;
    if (prev === 0) return '';
    var pct = Math.round((cur - prev) / Math.abs(prev) * 100);
    if (pct === 0) return '';
    var pos = pct > 0;
    if (invert) pos = !pos;
    return '<div class="delta" style="color:' + (pos ? '#2ea44f' : '#d73a49') + '">' +
      (pct > 0 ? '\u2191' : '\u2193') + ' ' + Math.abs(pct) + '%</div>';
  }

  var netSign = metrics.netLines >= 0 ? '+' : '';
  var cards = [
    {value: metrics.totalCommits.toLocaleString(), label: 'Commits', delta: deltaHtml('totalCommits', prior)},
    {value: '+' + metrics.totalAdditions.toLocaleString(), label: 'Lines Added', color: '#2ea44f', delta: deltaHtml('totalAdditions', prior)},
    {value: '-' + metrics.totalDeletions.toLocaleString(), label: 'Lines Deleted', color: '#d73a49', delta: deltaHtml('totalDeletions', prior, true)},
    {value: netSign + metrics.netLines.toLocaleString(), label: 'Net Lines', delta: deltaHtml('netLines', prior)},
    {value: metrics.activeDayPct + '%', label: 'Active Days (' + metrics.activeDays + '/' + metrics.totalDays + ')', delta: deltaHtml('activeDayPct', prior)},
    {value: String(metrics.currentStreak), label: 'Current Streak (days)'},
    {value: String(metrics.longestStreak), label: 'Longest Streak (days)'},
    {value: String(metrics.avgGap), label: 'Avg Gap (days)'}
  ];

  cards.forEach(function(card) {
    var div = document.createElement('div');
    div.className = 'card';
    var html = '<div class="value"' + (card.color ? ' style="color:' + card.color + '"' : '') + '>' + card.value + '</div>';
    html += '<div class="label">' + card.label + '</div>';
    if (card.delta) html += card.delta;
    div.innerHTML = html;
    container.appendChild(div);
  });
}

// ── Section 4: UI ──────────────────────────────────────────────────────

function populateTimezones(defaultTz) {
  var sel = document.getElementById('ctrl-timezone');
  sel.innerHTML = '';
  var tzList;
  try { tzList = Intl.supportedValuesOf('timeZone'); } catch(e) { tzList = [defaultTz]; }
  tzList.forEach(function(tz) {
    var opt = document.createElement('option');
    opt.value = tz;
    opt.textContent = tz;
    if (tz === defaultTz) opt.selected = true;
    sel.appendChild(opt);
  });
  if (sel.value !== defaultTz) {
    var opt = document.createElement('option');
    opt.value = defaultTz;
    opt.textContent = defaultTz;
    opt.selected = true;
    sel.prepend(opt);
  }
}

function setupControls() {
  var timer;
  function debouncedRender() {
    clearTimeout(timer);
    timer = setTimeout(renderAll, 150);
  }
  ['ctrl-since', 'ctrl-until', 'ctrl-granularity', 'ctrl-timezone'].forEach(function(id) {
    document.getElementById(id).addEventListener('change', debouncedRender);
  });
}

// ── Section 5: Orchestrator ────────────────────────────────────────────

function renderAll() {
  var since = document.getElementById('ctrl-since').value;
  var until = document.getElementById('ctrl-until').value;
  var gran = document.getElementById('ctrl-granularity').value;
  var tz = document.getElementById('ctrl-timezone').value;
  if (gran === 'auto') gran = resolveGranularity(since, until);

  var filtered = filterCommits(STATE.allCommits, since, until, tz);
  var daily = buildDaily(filtered, since, until, tz);
  var agg = aggregateData(daily, gran);
  var metrics = computeMetrics(daily, since, until);
  var prior = computePeriodComparison(STATE.allCommits, since, until, tz);

  renderCards(metrics, prior);
  renderHeatmap(daily);
  renderLinesChart(agg, gran);
  renderCommitsChart(agg, gran);
  renderDOW(daily);
  renderHourChart(filtered, tz);
  renderRepoBreakdown(filtered);
  renderActiveDaysChart(daily, gran);
  renderTopCommits(filtered);
}

// Dark mode re-render
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function() {
  renderAll();
});

// Initialize
loadData();
setupControls();
renderAll();

})();
"""


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def _safe_json(data) -> str:
    """Serialize to JSON, escaping </ for safe embedding in HTML script tags."""
    return json.dumps(data, default=str).replace("</", "<\\/")


def generate_report(
    commits: list[dict],
    since: date,
    until: date,
    granularity: str,
    output_path: str,
    username: str,
    tz=None,
    use_cdn: bool = False,
    all_cached_commits: list[dict] | None = None,
) -> None:
    """Generate a self-contained interactive HTML report.

    Embeds all commit data as JSON and renders everything client-side
    with interactive controls for date range, granularity, and timezone.
    """
    embed_commits = all_cached_commits if all_cached_commits else commits

    # Timezone name for JS (IANA name from ZoneInfo, or None for browser default)
    tz_name = getattr(tz, "key", None)

    initial_state = {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "granularity": granularity,
        "timezone": tz_name,
        "username": username,
    }

    commit_json = _safe_json(embed_commits)
    state_json = _safe_json(initial_state)

    # Plotly JS: embed inline or use CDN
    if use_cdn:
        plotly_script = '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>'
    else:
        plotly_js_content = plotly.offline.get_plotlyjs()
        plotly_script = f"<script>{plotly_js_content}</script>"

    safe_username = escape(username)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GitHub Activity: {safe_username}</title>
{plotly_script}
<style>
{_CSS}
</style>
</head>
<body>
<h1>GitHub Activity: {safe_username}</h1>
<div class="controls">
  <label>From: <input type="date" id="ctrl-since"></label>
  <label>To: <input type="date" id="ctrl-until"></label>
  <label>Gran:
    <select id="ctrl-granularity">
      <option value="auto">Auto</option>
      <option value="day">Day</option>
      <option value="week">Week</option>
      <option value="month">Month</option>
    </select>
  </label>
  <label>TZ: <select id="ctrl-timezone"></select></label>
</div>
<div id="cards" class="cards"></div>
<div class="chart-section" id="heatmap-section">
  <h3>Contribution Calendar</h3>
  <div id="heatmap-container" class="chart-container"></div>
</div>
<div class="chart-section" id="lines-section">
  <h3>Lines Changed</h3>
  <div id="lines-chart" class="chart-container"></div>
</div>
<div class="chart-section" id="commits-section">
  <h3>Commits</h3>
  <div id="commits-chart" class="chart-container"></div>
</div>
<div class="chart-row">
  <div class="chart-section chart-half" id="dow-section">
    <h3>Commits by Day of Week</h3>
    <div id="dow-chart" class="chart-container"></div>
  </div>
  <div class="chart-section chart-half" id="hour-section">
    <h3>Commits by Hour of Day</h3>
    <div id="hour-chart" class="chart-container"></div>
  </div>
</div>
<div class="chart-section" id="repo-section">
  <h3>Lines Changed by Repository</h3>
  <div id="repo-breakdown" class="chart-container"></div>
</div>
<div class="chart-section" id="active-days-section">
  <h3>Active Days</h3>
  <div id="active-days-chart" class="chart-container"></div>
</div>
<div class="chart-section" id="top-commits-section">
  <h3>Top Commits by Lines Changed</h3>
  <div id="top-commits" class="chart-container"></div>
</div>
<div class="footer">
  Generated by gh-activity &middot; {date.today().isoformat()}
</div>
<script type="application/json" id="commit-data">{commit_json}</script>
<script type="application/json" id="initial-state">{state_json}</script>
<script>
{_JS_APP}
</script>
</body>
</html>"""

    with open(output_path, "w") as f:
        f.write(html)
