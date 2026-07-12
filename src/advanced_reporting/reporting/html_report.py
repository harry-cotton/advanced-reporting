"""Single-file HTML client report — the emailable deliverable.

One self-contained file (inline CSS, base64 PNG charts, zero external scripts) built
from the SAME deterministic layers the dashboard renders: ``insights.py`` payloads,
the tier scorecard, the report spec's framing (KPI label, block order, targets), the
agent watch flags, and — when published and hash-current — the stamped AI commentary.
Building the report makes NO model calls: the commentary is embedded from
``outputs/commentary_ai.md`` if it exists, else the section carries an honest note.

Charts are matplotlib (Agg) rendered to base64 — static images are the right form
for a file that gets emailed and opened anywhere; the interactive versions live on
the dashboard. House tokens come straight from ``dashboard/theme.py``.
"""
from __future__ import annotations

import base64
import html
import io
import re
from datetime import date
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from ..agent.commentary_agent import STAMP, load_active_commentary  # noqa: E402
from ..agent.spec_agent import load_active_spec  # noqa: E402
from ..agent.validate import BLOCK_CATALOG  # noqa: E402
from ..dashboard import insights, theme  # noqa: E402
from ..utils import load_config, project_root, scope_to_sources  # noqa: E402

REPORT_PATH = Path("outputs/client_report.html")

_MPL_FONT = {"family": "sans-serif", "size": 10}
_FIG_DPI = 150


# ---------------------------------------------------------------- chart helpers
def _fig_to_b64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=_FIG_DPI, bbox_inches="tight",
                facecolor="white")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _style_axes(ax) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(theme.GRID)
    ax.tick_params(colors=theme.INK_SOFT, labelsize=9)
    ax.yaxis.grid(True, color=theme.GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def _img(b64: str, alt: str) -> str:
    return (f'<img alt="{html.escape(alt)}" style="width:100%;height:auto" '
            f'src="data:image/png;base64,{b64}">')


def _chart_claims(per: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    x = range(len(per))
    labels = [insights.channel_label(c) for c in per["channel"]]
    ax.bar([i - 0.2 for i in x], per["claimed"], width=0.38,
           color=theme.CLAIMED, label="Platform-claimed")
    ax.bar([i + 0.2 for i in x], per["measured"], width=0.38,
           color=theme.MEASURED, label="Analytics-measured")
    for i, r in enumerate(per.itertuples()):
        ax.text(i - 0.2, r.claimed, f"{r.ratio:.1f}x", ha="center", va="bottom",
                fontsize=8, color=theme.INK_SOFT)
    ax.set_xticks(list(x), labels)
    ax.legend(frameon=False, fontsize=9, loc="upper right")
    _style_axes(ax)
    return _img(_fig_to_b64(fig), "Platform-claimed vs analytics-measured by channel")


def _chart_costper(per: pd.DataFrame) -> str:
    per = per.sort_values("cost_per", ascending=True)
    fig, ax = plt.subplots(figsize=(7.2, 0.6 + 0.5 * len(per)))
    labels = [insights.channel_label(c) for c in per["channel"]]
    colors = [theme.channel_color(c, i) for i, c in enumerate(per["channel"])]
    ax.barh(labels, per["cost_per"], color=colors, height=0.55)
    for i, v in enumerate(per["cost_per"]):
        ax.text(v, i, f"  {insights._money(v)}", va="center", fontsize=9,
                color=theme.INK)
    ax.xaxis.grid(True, color=theme.GRID, linewidth=0.6)
    ax.yaxis.grid(False)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(theme.GRID)
    ax.tick_params(colors=theme.INK_SOFT, labelsize=9)
    ax.set_axisbelow(True)
    return _img(_fig_to_b64(fig), "Cost per outcome by channel")


def _chart_trend(series: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(7.2, 3.0))
    colors = {"Paid media": theme.ACCENT, insights.NONPAID_LABEL: theme.GRID}
    series.plot.area(ax=ax, stacked=True, linewidth=0,
                     color=[colors.get(c, theme.INK_SOFT) for c in series.columns])
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    _style_axes(ax)
    ax.set_xlabel("")
    return _img(_fig_to_b64(fig), "Outcome trend, paid vs organic & direct")


def _chart_mix(mix: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(4.4, 3.4))
    colors = [theme.channel_color(c, i) for i, c in enumerate(mix["channel"])]
    wedges, _texts, autotexts = ax.pie(
        mix["spend"], colors=colors, autopct="%1.0f%%", pctdistance=0.78,
        startangle=90, counterclock=False,
        wedgeprops=dict(width=0.42, edgecolor="white"))
    for t in autotexts:
        t.set_fontsize(8)
        t.set_color("white")
    ax.legend(wedges, [insights.channel_label(c) for c in mix["channel"]],
              frameon=False, fontsize=9, loc="center left",
              bbox_to_anchor=(1.0, 0.5))
    return _img(_fig_to_b64(fig), "Spend mix by channel")


# ---------------------------------------------------------------- tiny markdown
def _md_to_html(md: str) -> str:
    """Just enough markdown for our own artifacts (##, **, _, -, ×) — escaped
    first, so nothing in the commentary can inject markup."""
    out: list[str] = []
    in_list = False
    for raw in md.splitlines():
        line = html.escape(raw.rstrip())
        line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        line = re.sub(r"(?<![\w_])_([^_]+)_(?![\w_])", r"<em>\1</em>", line)
        if line.startswith("## "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h3>{line[3:]}</h3>")
        elif line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{line[2:]}</li>")
        elif line:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p>{line}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


# ---------------------------------------------------------------- section builders
def _section(title: str, body_html: str) -> str:
    return f'<section><h2>{html.escape(title)}</h2>\n{body_html}</section>'


def _tiles_html(tiles: list[dict]) -> str:
    cells = []
    for t in tiles:
        delta = (f'<div class="delta">{html.escape(t["delta"])}</div>'
                 if t.get("delta") else "")
        cells.append(
            f'<div class="tile"><div class="tile-label">{html.escape(t["label"])}'
            f'</div><div class="tile-value">{html.escape(t["value"])}</div>'
            f'{delta}</div>')
    return f'<div class="tiles">{"".join(cells)}</div>'


def _scorecard_html(sc: dict) -> str:
    rows = "".join(
        f'<tr><td>{html.escape(r["label"])}</td>'
        f'<td class="num">{html.escape(r["value_str"])}</td>'
        f'<td><span class="chip chip-{r["verdict"]}">{r["verdict"]}</span></td>'
        f'<td class="soft">{"configured target" if r["mode"] == "absolute" else "channel spread"}</td></tr>'
        for r in sc["rag"])
    grid = "".join(f'<tr><td>{html.escape(k)}</td>'
                   f'<td class="num">{html.escape(v)}</td><td></td><td></td></tr>'
                   for k, v in sc["grid"])
    if not rows and not grid:
        return ""
    return _section(
        f"{sc['label']} scorecard",
        '<table><thead><tr><th>Metric</th><th class="num">Value</th>'
        "<th>Verdict</th><th>Graded against</th></tr></thead>"
        f"<tbody>{rows}{grid}</tbody></table>")


# ---------------------------------------------------------------- the report
def build_report(root: Path | None = None) -> Path:
    root = root or project_root()
    weekly_f = root / "data" / "processed" / "channel_weekly_metrics.csv"
    if not weekly_f.exists():
        raise FileNotFoundError(
            "no processed data — run scripts/run_pipeline.py first")
    weekly = pd.read_csv(weekly_f, parse_dates=["date"])
    cfg = load_config()
    hist_f = root / "data" / "processed" / "history.parquet"
    hist = (scope_to_sources(pd.read_parquet(hist_f), cfg)
            if hist_f.exists() else None)

    rep = cfg.get("reporting") or {}
    spec, spec_note = load_active_spec(root)
    kpi_label = rep.get("kpi_label") or spec.get("kpi_label") or "key events"
    targets = {**(spec.get("targets") or {}), **(rep.get("targets") or {})}
    tier = spec.get("primary_tier") or (
        "outcome" if insights._has_measured(weekly) else "reach")
    project = (cfg.get("project") or {}).get("name", "Campaign report")

    lo, hi = weekly["date"].min(), weekly["date"].max()
    plt.rc("font", **_MPL_FONT)

    # --- blocks in spec order (same catalog as the dashboard) ---------------------
    def _b_kpi_trend() -> str:
        b = insights.kpi_trend_insight(weekly, kpi_label)
        if not b:
            return ""
        return _section(b["title"], _chart_trend(b["series"])
                        + _md_to_html(b["narrative"]))

    def _b_claims() -> str:
        b = insights.claims_vs_measured_insight(weekly, kpi_label)
        if not b:
            return ""
        return _section(b["title"], _chart_claims(b["per_channel"])
                        + _md_to_html(b["narrative"]))

    def _b_costper() -> str:
        b = insights.cost_per_outcome_insight(weekly, kpi_label)
        if not b:
            return ""
        return _section(b["title"], _chart_costper(b["per_channel"])
                        + _md_to_html(b["narrative"]))

    def _b_audience() -> str:
        if hist is None:
            return ""
        b = insights.audience_callout_insight(hist)
        if not b:
            return ""
        return _section(b["title"], _md_to_html(b["narrative"]))

    def _b_pacing() -> str:
        b = insights.pacing_insight(weekly, rep.get("budget"))
        if not b:
            return ""
        mix = insights.spend_mix(weekly)
        return _section(b["title"], _chart_mix(mix) + _md_to_html(b["narrative"]))

    renderers = {"kpi_trend": _b_kpi_trend, "claims_vs_measured": _b_claims,
                 "cost_per_outcome": _b_costper, "audience_callout": _b_audience,
                 "pacing": _b_pacing}
    blocks_html = "".join(renderers[n]() for n in (spec.get("blocks")
                                                   or BLOCK_CATALOG))

    # --- surrounding matter --------------------------------------------------------
    tiles = _tiles_html(insights.headline_tiles(weekly, kpi_label))
    lede = _md_to_html(insights.topline_summary(weekly, kpi_label))
    scorecard = _scorecard_html(
        insights.tier_scorecard(weekly, tier, targets=targets, kpi_label=kpi_label))

    flags_html = ""
    if spec.get("watch_flags"):
        items = "".join(f"<li>{html.escape(f)}</li>" for f in spec["watch_flags"])
        flags_html = (
            '<aside class="flags"><h2>Analyst watch flags</h2>'
            '<p class="soft">AI-selected from computed evidence — review before '
            f"client use.</p><ul>{items}</ul></aside>")

    commentary, c_note = load_active_commentary(root)
    if commentary:
        ai_html = (
            '<section class="ai"><h2>Commentary</h2>'
            f'<p class="stamp">{html.escape(STAMP)}. Every number below was '
            "checked against the computed facts before publication; "
            "recommendations come only from the deterministically-eligible menu."
            f"</p>{_md_to_html(commentary)}</section>")
    else:
        note = c_note or ("No AI commentary was published for this run — the "
                          "deterministic narrative above is the complete report.")
        ai_html = f'<section class="ai"><p class="soft">{html.escape(note)}</p></section>'

    spec_line = ("Layout, labels and gauge bands arranged by the report-spec agent; "
                 "every number is computed deterministically."
                 if spec else (spec_note or "Deterministic default layout."))

    css = f"""
    body {{ font-family: {theme.SANS}; color: {theme.INK}; background: {theme.PAPER};
           max-width: 860px; margin: 0 auto; padding: 32px 20px; line-height: 1.55; }}
    h1 {{ font-family: {theme.SERIF}; font-size: 2.1rem; margin: 0 0 4px; }}
    h2 {{ font-family: {theme.SERIF}; font-size: 1.25rem; margin: 0 0 10px; }}
    h3 {{ font-size: 1.02rem; margin: 18px 0 6px; }}
    section {{ margin: 30px 0; padding-top: 18px; border-top: 1px solid {theme.GRID}; }}
    .soft {{ color: {theme.INK_SOFT}; font-size: 0.9rem; }}
    .tiles {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 18px 0; }}
    .tile {{ flex: 1 1 140px; background: {theme.PAPER_TINT}; border-radius: 10px;
            padding: 12px 14px; }}
    .tile-label {{ font-size: 0.72rem; letter-spacing: 0.06em; text-transform: uppercase;
                  color: {theme.INK_SOFT}; }}
    .tile-value {{ font-size: 1.5rem; font-weight: 600; }}
    .delta {{ font-size: 0.8rem; color: {theme.INK_SOFT}; }}
    .lede p {{ font-size: 1.05rem; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 0.92rem; }}
    th, td {{ text-align: left; padding: 7px 10px; border-bottom: 1px solid {theme.GRID}; }}
    th {{ color: {theme.INK_SOFT}; font-weight: 600; font-size: 0.8rem; }}
    td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .chip {{ padding: 2px 9px; border-radius: 999px; font-size: 0.78rem; }}
    .chip-good {{ background: {theme.BAND_FILL['good']}; color: {theme.VERDICT_INK['good']}; }}
    .chip-warn {{ background: {theme.BAND_FILL['warn']}; color: {theme.VERDICT_INK['warn']}; }}
    .chip-bad {{ background: {theme.BAND_FILL['bad']}; color: {theme.VERDICT_INK['bad']}; }}
    .flags {{ background: {theme.PAPER_TINT}; border-radius: 10px; padding: 16px 20px;
             margin: 26px 0; }}
    .flags ul {{ margin: 8px 0 0 18px; padding: 0; }}
    .ai {{ background: {theme.PAPER_TINT}; border-radius: 10px; padding: 20px 24px; }}
    .stamp {{ color: {theme.VERDICT_INK['warn']}; font-size: 0.85rem;
             border-bottom: 1px solid {theme.GRID}; padding-bottom: 10px; }}
    footer {{ margin-top: 36px; padding-top: 14px; border-top: 1px solid {theme.GRID};
             color: {theme.INK_SOFT}; font-size: 0.82rem; }}
    """

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(project)} — {lo:%d %b %Y} to {hi:%d %b %Y}</title>
<style>{css}</style></head>
<body>
<header>
  <h1>How the campaign is doing</h1>
  <p class="soft">{html.escape(project)} · {lo:%d %b %Y} – {hi:%d %b %Y} ·
  {len(insights._paid_channels(weekly))} paid channels · {html.escape(spec_line)}</p>
</header>
{tiles}
<div class="lede">{lede}</div>
{flags_html}
{blocks_html}
{scorecard}
{ai_html}
<footer>
  <p>Attribution legend: <strong style="color:{theme.CLAIMED}">platform-claimed</strong>
  = the ad platforms' own conversion counts (self-attributed);
  <strong style="color:{theme.MEASURED}">analytics-measured</strong> = one consistent
  yardstick across channels; modeled (MMM) figures, when present, carry 90% intervals.
  None are proof of incrementality on their own.</p>
  <p>Generated {date.today():%d %b %Y} · Advanced Reporting · data-quality report
  available on request.</p>
</footer>
</body></html>"""

    out = root / REPORT_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    return out
