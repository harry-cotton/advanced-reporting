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
from ..agent.recommendations import eligible_recommendations  # noqa: E402
from ..agent.spec_agent import load_active_spec  # noqa: E402
from ..agent.validate import BLOCK_CATALOG  # noqa: E402
from ..dashboard import insights, theme  # noqa: E402
from ..dashboard.mmm_view import load_mmm  # noqa: E402
from ..utils import (load_config, load_pipeline_stages, project_root,  # noqa: E402
                     scope_to_sources)

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


def _chart_pipeline(stages: pd.DataFrame) -> str:
    """Horizontal 6-stage applicant funnel: CRM-measured counts + pass-through."""
    df = stages.iloc[::-1]          # first gate at the top
    fig, ax = plt.subplots(figsize=(7.2, 0.6 + 0.5 * len(df)))
    ax.barh(df["label"], df["value"], color=theme.MEASURED, height=0.55)
    for i, (v, r) in enumerate(zip(df["value"], df["step_rate"])):
        note = f"  {v:,.0f}" + (f"  ·  {r * 100:.0f}% of prior gate" if r == r else "")
        ax.text(v, i, note, va="center", fontsize=9, color=theme.INK)
    ax.set_xlim(0, float(df["value"].max()) * 1.45)
    ax.xaxis.grid(True, color=theme.GRID, linewidth=0.6)
    ax.yaxis.grid(False)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(theme.GRID)
    ax.tick_params(colors=theme.INK_SOFT, labelsize=9)
    ax.set_axisbelow(True)
    return _img(_fig_to_b64(fig), "Applicant pipeline stages (CRM counts)")


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
        # truthful provenance: a benchmark band must never read as a "client target"
        f'<td class="soft">{html.escape(r.get("provenance", "channel spread"))}</td></tr>'
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


# ---------------------------------------------------------------- channel table + next steps
def _channel_summary_html(weekly: pd.DataFrame, kpi_label: str) -> str:
    """One row per channel: spend, outcome, cost/outcome, claimed, claim ratio — the
    forwardable artifact a client sends to finance. Built from the same insight payloads."""
    cp = insights.cost_per_outcome_insight(weekly, kpi_label)
    if not cp:
        return ""
    measured = insights._has_measured(weekly)
    ocol = "key_events" if measured else "conversions"
    olabel = kpi_label.capitalize() if measured else "Claimed conv."
    per = cp["per_channel"].copy()
    ratios = {}
    cv = insights.claims_vs_measured_insight(weekly, kpi_label)
    if cv is not None:
        for _, r in cv["per_channel"].iterrows():
            ratios[r["channel"]] = (float(r["claimed"]), float(r["ratio"]))
    body = []
    for _, r in per.iterrows():
        ch = r["channel"]
        claimed, ratio = ratios.get(ch, (None, None))
        extra = (f'<td class="num">{claimed:,.0f}</td><td class="num">{ratio:.1f}x</td>'
                 if claimed is not None else '<td></td><td></td>')
        body.append(
            f'<tr><td>{html.escape(insights.channel_label(ch))}</td>'
            f'<td class="num">{insights._money(r["spend"])}</td>'
            f'<td class="num">{float(r[ocol]):,.0f}</td>'
            f'<td class="num">{insights._money(r["cost_per"])}</td>{extra}</tr>')
    head = (f'<th>Channel</th><th class="num">Spend</th><th class="num">{html.escape(olabel)}'
            f'</th><th class="num">Cost / {html.escape(_singular(kpi_label))}</th>'
            '<th class="num">Platform-claimed</th><th class="num">Claim ratio</th>')
    return _section(
        "Channel scorecard",
        f'<table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'
        '<p class="soft">Cost per outcome is analytics-measured; platform-claimed and the '
        'claim ratio are the platforms\' self-reported counts on the same campaigns.</p>')


_REC_TITLES = {
    "unlock_mmm": "Unlock incrementality measurement",
    "investigate_tracking": "Investigate conversion tracking",
    "fix_naming": "Adopt the naming convention on unparsed ad sets",
    "shift_within_type": "Shift budget to the cheaper audience (same type)",
    "scale_with_test": "Scale the top channel with a controlled test",
    "cut_or_restructure": "Restructure the underperforming channel",
    "rebalance_channel_budget": "Rebalance channel budget",
}


def _next_steps_html(recs: list[dict]) -> str:
    """Deterministic 'What we'd do next' — plain-English titles over the eligibility
    engine's computed summaries (no LLM, no invented numbers)."""
    if not recs:
        return ""
    items = "".join(
        f'<li><strong>{html.escape(_REC_TITLES.get(r["type"], r["type"].replace("_", " ").capitalize()))}'
        f'</strong> — {html.escape(r.get("summary", ""))}</li>'
        for r in recs[:3])
    return _section("What we'd do next",
                    f'<ul>{items}</ul><p class="soft">Prioritised by impact; drawn from the '
                    'deterministic recommendation menu — never invented.</p>')


def _singular(label: str) -> str:
    return label[:-1] if label.endswith("s") else label


# ---------------------------------------------------------------- the report
def build_report(root: Path | None = None, audience: str | None = None) -> Path:
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
    stages = load_pipeline_stages(cfg, root)   # CRM applicant gates (may be None)

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

    def _b_pipeline() -> str:
        b = insights.recruiting_pipeline_insight(stages)
        if not b:
            return ""
        return _section(b["title"], _chart_pipeline(b["stages"])
                        + _md_to_html(b["narrative"]))

    renderers = {"kpi_trend": _b_kpi_trend, "claims_vs_measured": _b_claims,
                 "cost_per_outcome": _b_costper, "audience_callout": _b_audience,
                 "recruiting_pipeline": _b_pipeline, "pacing": _b_pacing}
    assert set(renderers) == set(BLOCK_CATALOG), \
        "html_report block renderers out of sync with agent BLOCK_CATALOG"
    blocks_html = "".join(renderers[n]() for n in (spec.get("blocks")
                                                   or BLOCK_CATALOG))

    # --- audience mode: client-safe by default (strip internal AI-governance language) --
    # explicit arg wins (tests / callers); else config; else client-safe.
    audience = str(audience or rep.get("report_audience", "client")).lower()
    client_mode = audience != "internal"

    # --- surrounding matter --------------------------------------------------------
    tiles = _tiles_html(insights.headline_tiles(weekly, kpi_label))
    lede = _md_to_html(insights.topline_summary(weekly, kpi_label))
    scorecard = _scorecard_html(insights.tier_scorecard(
        weekly, tier, targets=targets, kpi_label=kpi_label,
        config_target_keys=set(rep.get("targets") or {})))
    channel_tbl = _channel_summary_html(weekly, kpi_label)

    # deterministic next steps (no LLM): the eligibility engine's computed recommendations
    mmm = load_mmm(root / "outputs")
    unparsed = None
    try:
        from ..dashboard.drilldown import unparsed_stats
        unparsed = unparsed_stats(hist) if hist is not None else None
    except Exception:
        unparsed = None
    recs = eligible_recommendations(weekly, hist=hist, mmm=mmm, unparsed=unparsed)
    next_steps = _next_steps_html(recs)

    # analyst watch flags: internal only — a client deliverable never says "review before
    # client use" inside itself, nor exposes enum keys / internal filenames
    flags_html = ""
    if spec.get("watch_flags") and not client_mode:
        items = "".join(f"<li>{html.escape(f)}</li>" for f in spec["watch_flags"])
        flags_html = (
            '<aside class="flags"><h2>Analyst watch flags</h2>'
            '<p class="soft">AI-selected from computed evidence — review before '
            f"client use.</p><ul>{items}</ul></aside>")

    commentary, c_note = load_active_commentary(root)
    if commentary and client_mode:
        # client framing: confidence, not a "review before use" warning; and strip the
        # commentary's own "## Recommendations" (enum keys) — the plain-English "What we'd
        # do next" section already carries them
        body_client = commentary.split("## Recommendations")[0].rstrip()
        ai_html = (
            '<section class="ai"><h2>Commentary</h2>'
            '<p class="stamp-ok">AI-assisted commentary — every figure verified against '
            "this report's computed data.</p>"
            f"{_md_to_html(body_client)}</section>")
    elif commentary:
        ai_html = (
            '<section class="ai"><h2>Commentary</h2>'
            f'<p class="stamp">{html.escape(STAMP)}. Every number below was '
            "checked against the computed facts before publication; "
            "recommendations come only from the deterministically-eligible menu."
            f"</p>{_md_to_html(commentary)}</section>")
    elif client_mode:
        ai_html = ""      # no internal "nothing was published" note in a client report
    else:
        note = c_note or ("No AI commentary was published for this run — the "
                          "deterministic narrative above is the complete report.")
        ai_html = f'<section class="ai"><p class="soft">{html.escape(note)}</p></section>'

    # cover: "Prepared for [client] · [campaign]" when configured (reporting.client_name /
    # reporting.campaign_name); the methodology note is a quiet footnote, not the subtitle
    client_name = rep.get("client_name")
    campaign_name = rep.get("campaign_name")
    cover_bits = [b for b in (client_name, campaign_name) if b]
    cover_html = (f'<p class="cover">Prepared for {html.escape(" · ".join(cover_bits))}</p>'
                  if cover_bits else "")
    method_line = ("Figures computed deterministically from the weekly data; layout by the "
                   "report-spec agent." if spec
                   else (spec_note or "Deterministic default layout."))

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
    .stamp-ok {{ color: {theme.INK_SOFT}; font-size: 0.85rem;
             border-bottom: 1px solid {theme.GRID}; padding-bottom: 10px; }}
    .cover {{ font-family: {theme.SERIF}; font-size: 1.05rem; color: {theme.INK};
             margin: 2px 0 0; }}
    .steps li {{ margin: 6px 0; }}
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
  {cover_html}
  <p class="soft">{html.escape(project)} · {lo:%d %b %Y} – {hi:%d %b %Y} ·
  {len(insights._paid_channels(weekly))} paid channels · {html.escape(method_line)}</p>
</header>
{tiles}
<div class="lede">{lede}</div>
{flags_html}
{blocks_html}
{channel_tbl}
{scorecard}
{next_steps}
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
