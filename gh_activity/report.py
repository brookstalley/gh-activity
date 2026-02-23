"""Report generation: pandas aggregation, Plotly charts, HTML assembly."""

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def build_dataframe(commits: list[dict], since: date, until: date) -> pd.DataFrame:
    """Build a DataFrame from commit data with a complete date index."""
    if not commits:
        idx = pd.date_range(since, until, freq="D")
        return pd.DataFrame(
            {"commits": 0, "additions": 0, "deletions": 0},
            index=idx,
        )

    df = pd.DataFrame(commits)
    df["date"] = pd.to_datetime(df["date"])
    for col in ("additions", "deletions"):
        if col not in df.columns:
            df[col] = 0
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    daily = df.groupby("date").agg(
        commits=("sha", "count"),
        additions=("additions", "sum"),
        deletions=("deletions", "sum"),
    )

    # Reindex to full date range
    idx = pd.date_range(since, until, freq="D")
    daily = daily.reindex(idx, fill_value=0)
    daily.index.name = "date"
    return daily


def aggregate(daily: pd.DataFrame, granularity: str) -> pd.DataFrame:
    """Aggregate daily data to week or month granularity."""
    if granularity == "day":
        return daily
    freq = "W-MON" if granularity == "week" else "MS"
    return daily.resample(freq).sum()


def compute_metrics(daily: pd.DataFrame, since: date, until: date) -> dict:
    """Compute summary metrics from daily data."""
    total_days = (until - since).days + 1
    active_days = int((daily["commits"] > 0).sum())
    return {
        "total_commits": int(daily["commits"].sum()),
        "total_additions": int(daily["additions"].sum()),
        "total_deletions": int(daily["deletions"].sum()),
        "net_lines": int(daily["additions"].sum() - daily["deletions"].sum()),
        "active_days": active_days,
        "total_days": total_days,
        "active_day_pct": round(active_days / total_days * 100, 1) if total_days > 0 else 0,
    }


def make_heatmap(daily: pd.DataFrame) -> go.Figure:
    """GitHub-style contribution calendar heatmap."""
    df = daily.reset_index()
    df.columns = ["date", "commits", "additions", "deletions"]
    df["weekday"] = df["date"].dt.weekday  # 0=Mon, 6=Sun
    df["week"] = df["date"].dt.isocalendar().week.astype(int)
    df["year"] = df["date"].dt.year

    # Build week number that's monotonically increasing across year boundary
    min_date = df["date"].min()
    df["week_offset"] = ((df["date"] - min_date).dt.days) // 7

    fig = go.Figure(data=go.Heatmap(
        x=df["week_offset"],
        y=df["weekday"],
        z=df["commits"],
        colorscale=[
            [0, "#ebedf0"],
            [0.01, "#9be9a8"],
            [0.33, "#40c463"],
            [0.66, "#30a14e"],
            [1.0, "#216e39"],
        ],
        showscale=False,
        hovertemplate="%{customdata[0]}<br>%{z} commits<extra></extra>",
        customdata=df[["date"]].values,
        xgap=3,
        ygap=3,
    ))

    fig.update_layout(
        title="Contribution Calendar",
        yaxis=dict(
            tickvals=[0, 1, 2, 3, 4, 5, 6],
            ticktext=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            autorange="reversed",
        ),
        xaxis=dict(showticklabels=False),
        height=220,
        margin=dict(l=50, r=20, t=50, b=20),
    )
    return fig


def make_commits_chart(agg: pd.DataFrame, granularity: str) -> go.Figure:
    """Bar chart of commits over time."""
    df = agg.reset_index()
    df.columns = ["date", "commits", "additions", "deletions"]

    fig = go.Figure(data=go.Bar(
        x=df["date"],
        y=df["commits"],
        marker_color="#4078c0",
        hovertemplate="%{x|%Y-%m-%d}<br>%{y} commits<extra></extra>",
    ))

    fig.update_layout(
        title=f"Commits per {granularity}",
        xaxis_title="Date",
        yaxis_title="Commits",
        height=350,
        margin=dict(l=50, r=20, t=50, b=50),
    )
    return fig


def make_lines_chart(agg: pd.DataFrame, granularity: str) -> go.Figure:
    """Area chart: additions (green), deletions (red), net (blue line)."""
    df = agg.reset_index()
    df.columns = ["date", "commits", "additions", "deletions"]
    df["net"] = df["additions"] - df["deletions"]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["additions"],
        fill="tozeroy", name="Additions",
        line=dict(color="#2ea44f"), fillcolor="rgba(46,164,79,0.3)",
    ))
    fig.add_trace(go.Scatter(
        x=df["date"], y=-df["deletions"],
        fill="tozeroy", name="Deletions",
        line=dict(color="#d73a49"), fillcolor="rgba(215,58,73,0.3)",
    ))
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["net"],
        name="Net", line=dict(color="#0366d6", width=2),
    ))

    fig.update_layout(
        title=f"Lines changed per {granularity}",
        xaxis_title="Date",
        yaxis_title="Lines",
        height=350,
        margin=dict(l=50, r=20, t=50, b=50),
    )
    return fig


def make_active_days_chart(daily: pd.DataFrame) -> go.Figure:
    """Weekly active-day percentage bar chart."""
    weekly = daily.resample("W-MON").agg(
        active=("commits", lambda x: (x > 0).sum()),
        total=("commits", "count"),
    )
    weekly["pct"] = (weekly["active"] / weekly["total"] * 100).round(1)

    colors = [
        "#d73a49" if p < 30 else "#e36209" if p < 60 else "#2ea44f"
        for p in weekly["pct"]
    ]

    fig = go.Figure(data=go.Bar(
        x=weekly.index,
        y=weekly["pct"],
        marker_color=colors,
        hovertemplate="%{x|%Y-%m-%d}<br>%{y:.1f}% active<extra></extra>",
    ))

    fig.update_layout(
        title="Active-Day % by Week",
        xaxis_title="Week",
        yaxis_title="% of days active",
        yaxis=dict(range=[0, 100]),
        height=300,
        margin=dict(l=50, r=20, t=50, b=50),
    )
    return fig


def generate_report(
    commits: list[dict],
    since: date,
    until: date,
    granularity: str,
    output_path: str,
    username: str,
) -> None:
    """Generate a self-contained interactive HTML report."""
    daily = build_dataframe(commits, since, until)
    agg = aggregate(daily, granularity)
    metrics = compute_metrics(daily, since, until)

    heatmap = make_heatmap(daily)
    commits_chart = make_commits_chart(agg, granularity)
    lines_chart = make_lines_chart(agg, granularity)
    active_chart = make_active_days_chart(daily)

    # Convert charts to HTML divs
    heatmap_html = heatmap.to_html(full_html=False, include_plotlyjs=False)
    commits_html = commits_chart.to_html(full_html=False, include_plotlyjs=False)
    lines_html = lines_chart.to_html(full_html=False, include_plotlyjs=False)
    active_html = active_chart.to_html(full_html=False, include_plotlyjs=False)

    # Get plotly.js CDN URL
    plotly_js = "https://cdn.plot.ly/plotly-2.35.2.min.js"

    net_sign = "+" if metrics["net_lines"] >= 0 else ""
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GitHub Activity: {username}</title>
<script src="{plotly_js}"></script>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    max-width: 1200px;
    margin: 0 auto;
    padding: 20px;
    background: #f6f8fa;
    color: #24292e;
  }}
  h1 {{ border-bottom: 1px solid #e1e4e8; padding-bottom: 10px; }}
  .cards {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
    margin: 20px 0;
  }}
  .card {{
    background: white;
    border: 1px solid #e1e4e8;
    border-radius: 6px;
    padding: 16px;
    text-align: center;
  }}
  .card .value {{
    font-size: 2em;
    font-weight: 600;
    color: #0366d6;
  }}
  .card .label {{
    font-size: 0.9em;
    color: #586069;
    margin-top: 4px;
  }}
  .chart-container {{
    background: white;
    border: 1px solid #e1e4e8;
    border-radius: 6px;
    padding: 10px;
    margin: 16px 0;
  }}
  .footer {{
    text-align: center;
    color: #586069;
    font-size: 0.85em;
    margin-top: 30px;
    padding-top: 10px;
    border-top: 1px solid #e1e4e8;
  }}
</style>
</head>
<body>
<h1>GitHub Activity: {username}</h1>
<p>{since.isoformat()} to {until.isoformat()} &middot; granularity: {granularity}</p>

<div class="cards">
  <div class="card">
    <div class="value">{metrics['total_commits']:,}</div>
    <div class="label">Commits</div>
  </div>
  <div class="card">
    <div class="value" style="color: #2ea44f">+{metrics['total_additions']:,}</div>
    <div class="label">Lines Added</div>
  </div>
  <div class="card">
    <div class="value" style="color: #d73a49">-{metrics['total_deletions']:,}</div>
    <div class="label">Lines Deleted</div>
  </div>
  <div class="card">
    <div class="value">{net_sign}{metrics['net_lines']:,}</div>
    <div class="label">Net Lines</div>
  </div>
  <div class="card">
    <div class="value">{metrics['active_day_pct']}%</div>
    <div class="label">Active Days ({metrics['active_days']}/{metrics['total_days']})</div>
  </div>
</div>

<div class="chart-container">{heatmap_html}</div>
<div class="chart-container">{commits_html}</div>
<div class="chart-container">{lines_html}</div>
<div class="chart-container">{active_html}</div>

<div class="footer">
  Generated by gh-activity &middot; {date.today().isoformat()}
</div>
</body>
</html>"""

    with open(output_path, "w") as f:
        f.write(html)
