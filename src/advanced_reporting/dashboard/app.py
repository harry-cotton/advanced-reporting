"""Overview — the editorial narrative landing page (dashboard redesign R2).

A scrollable story of 3–4 insight blocks, each headed by a serif ACTION TITLE (an
insight sentence, never a label), an annotated house-style chart, and a woven
commentary paragraph. Every number is computed deterministically from the weekly
tables (`dashboard/insights.py`) — no fabricated commentary. The signature visual is
the platform-claims-vs-analytics-measured gap. Dense interactive drill-downs live on
the sub-pages (Explore; Channels/Audiences/Data quality arrive in R3).

Run:  streamlit run src/advanced_reporting/dashboard/app.py
"""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from advanced_reporting.agent import load_active_spec  # noqa: E402
from advanced_reporting.agent.commentary_agent import (  # noqa: E402
    STAMP, load_active_commentary, load_active_commentary_sections)
from advanced_reporting.agent.validate import BLOCK_CATALOG  # noqa: E402
from advanced_reporting.dashboard import filters, insights, theme  # noqa: E402
from advanced_reporting.reporting import lens as L  # noqa: E402
from advanced_reporting.utils import load_config  # noqa: E402

st.set_page_config(page_title="Advanced Reporting — Exec Summary", layout="wide")
theme.inject_css()
theme.nav_bar()

metrics_f = ROOT / "data" / "processed" / "channel_weekly_metrics.csv"
history_f = ROOT / "data" / "processed" / "history.parquet"
if not metrics_f.exists():
    st.title("Advanced Reporting")
    st.warning("No processed data yet. Run `python scripts/run_pipeline.py` first.")
    st.stop()


@st.cache_data
def _load_weekly(path: str, mtime: float) -> pd.DataFrame:
    return pd.read_csv(path, parse_dates=["date"])


@st.cache_data
def _load_hist(path: str, mtime: float) -> pd.DataFrame:
    return pd.read_parquet(path)


@st.cache_data
def _parse_lens_cached(text: str) -> L.ReportSpec:
    return L.parse_lens(text, use_llm=False)


@st.cache_data
def _load_stages(mtime: float | None) -> pd.DataFrame | None:
    """CRM applicant-pipeline stage counts (post-submission gates), or None."""
    from advanced_reporting.utils import load_pipeline_stages
    return load_pipeline_stages(load_config(), ROOT)


from advanced_reporting.utils import scope_to_sources  # noqa: E402

weekly_all = _load_weekly(str(metrics_f), metrics_f.stat().st_mtime)
hist_all = _load_hist(str(history_f), history_f.stat().st_mtime) if history_f.exists() else None
cfg = load_config()
# the weekly csv is pipeline-scoped already; scope the raw store the same way so a
# mixed store (synthetic + client drops) can't leak other sources into this report
hist_all = scope_to_sources(hist_all, cfg)
rep = cfg.get("reporting", {}) or {}
_stages_rel = (cfg.get("data") or {}).get("pipeline_stages_path")
_stages_f = (ROOT / _stages_rel) if _stages_rel else None
_stages_df = _load_stages(_stages_f.stat().st_mtime
                          if _stages_f is not None and _stages_f.exists() else None)
# The report spec (outputs/report_spec.json, written by scripts/advise.py --spec)
# fills the gaps config leaves; explicit config keys always win. No spec -> {}.
spec, spec_note = load_active_spec(ROOT)
kpi_label = rep.get("kpi_label") or spec.get("kpi_label") or "key events"
budget_cfg = rep.get("budget")

# --- global sidebar filters (date + channel, carry across every tab) ---------------
_dr, _chsel = filters.sidebar_filters(
    weekly_all["channel"].unique(),
    weekly_all["date"].min().date(), weekly_all["date"].max().date())
weekly = filters.apply(weekly_all, _dr, _chsel)
hist = filters.apply(hist_all, _dr, _chsel) if hist_all is not None else None
if weekly.empty:
    st.title("How the campaign is doing")
    st.info("No rows for the current filter — widen the date range or channel selection "
            "in the sidebar.")
    st.stop()

# --- masthead -------------------------------------------------------------------
st.title("How the campaign is doing")
lo, hi = weekly["date"].min(), weekly["date"].max()
n_paid = len(insights._paid_channels(weekly))
_ai_on = bool(rep.get("ai_commentary"))
st.caption(f"{lo:%d %b %Y} – {hi:%d %b %Y} · {n_paid} paid channels · outcome measured "
           f"as **{kpi_label}**. Click any channel to focus the whole dashboard on it.")
if spec_note:
    st.caption(f"⚠ {spec_note}")
# Methodology + the agent's watch flags are analyst matter — relocated below the fold
# (see the end of the page) so they don't sit above the numbers a CMO reads first.
_watch_flags = spec.get("watch_flags") if spec else None
filters.focus_chip()

# --- executive hero + supporting tiles ---------------------------------------------
# One number owns the top: the primary outcome, large, with an honest sparkline (the
# flight's part-weeks trimmed so the trend doesn't show a false end-cliff). The rest
# support it. Falls back to a plain row if the outcome tile can't be identified.
tiles = insights.headline_tiles(weekly, kpi_label)
_hero = next((t for t in tiles if t["delta_color"] == "normal"), tiles[0] if tiles else None)
if _hero is not None:
    _support = [t for t in tiles if t is not _hero]
    _ocol = "key_events" if insights._has_measured(weekly) else "conversions"
    _ser = (weekly[weekly["channel"].isin(insights._paid_channels(weekly))]
            .groupby("date")[_ocol].sum(min_count=1).sort_index())
    _ser = _ser.drop([e for e in insights._partial_edge_weeks(_ser) if e in _ser.index])
    hcol, scol = st.columns([1.25, 2.4])
    with hcol:
        theme.hero_card(_hero["label"], _hero["value"], delta=_hero["delta"],
                        delta_color=_hero["delta_color"], spark=_ser.tolist(),
                        sub=("analytics-measured, paid campaigns"
                             if insights._has_measured(weekly) else "platform-claimed"))
    with scol:
        scols = st.columns(len(_support))
        for c, t in zip(scols, _support):
            with c:
                theme.metric_card(t["label"], t["value"], delta=t["delta"],
                                  delta_color=t["delta_color"], help=t.get("help"))
else:
    cols = st.columns(len(tiles))
    for col, t in zip(cols, tiles):
        with col:
            theme.metric_card(t["label"], t["value"], delta=t["delta"],
                              delta_color=t["delta_color"], help=t.get("help"))
st.divider()

# --- the hero chart: monthly spend + cost per outcome (the "how are we doing" read) ---
_eff = insights.spend_efficiency_trend(weekly, kpi_label)
if _eff:
    theme.action_title(_eff["title"],
                       "Monthly paid spend (bars) with cost per analytics-measured "
                       f"{insights._singular(kpi_label)} overlaid (line).")
    _mo = _eff["monthly"]
    theme.combo(_mo["month"], _mo["spend"], _mo["cost_per"],
                bar_name="Spend", line_name=f"Cost / {insights._singular(kpi_label)}",
                bar_fmt="currency", line_fmt="$,.0f", y2_title="Cost / outcome",
                height=300)
    st.divider()

theme.lede(insights.topline_summary(weekly, kpi_label))

# --- tier scorecard: Awareness / Engagement / Action goal gauges -------------------
_TIER_BY_LABEL = {"Awareness": "reach", "Engagement": "intent", "Action": "outcome"}
# The spec's primary_tier picks which tier the scorecard OPENS on (config has no
# such key, so the spec can't be overridden here — the user can always click).
_LABEL_BY_TIER = {v: k for k, v in _TIER_BY_LABEL.items()}
_default_label = _LABEL_BY_TIER.get(spec.get("primary_tier"), "Awareness")
st.markdown("")  # small breath before the segmented control
_tier_label = st.segmented_control(
    "View by tier", list(_TIER_BY_LABEL), default=_default_label,
    key="_tier_lens", label_visibility="collapsed",
    help="Awareness → reach, Engagement → intent, Action → outcome (the KPI pyramid).")
_tier = _TIER_BY_LABEL.get(_tier_label or _default_label, "reach")
# targets: spec fills the gaps, explicit config wins per metric key. config_target_keys
# lets the scorecard label a config band "client target" vs a spec band "industry benchmark".
_targets = {**(spec.get("targets") or {}), **(rep.get("targets") or {})}
_cfg_keys = set(rep.get("targets") or {})
_sc = insights.tier_scorecard(weekly, _tier, targets=_targets, kpi_label=kpi_label,
                              config_target_keys=_cfg_keys)


def _tier_heading(sc: dict) -> str:
    """An insight heading (not a template label): the tier's headline metric + verdict."""
    r = sc["rag"][0] if sc["rag"] else None
    if not r:
        return f"{sc['label']} — not measured in the current data yet"
    phrase = {"good": "comfortably inside", "warn": "pressing against",
              "bad": "outside"}.get(r["verdict"], "against")
    return f"{sc['label']} — {r['label']} {r['value_str']}, {phrase} the {r['provenance']}"


theme.action_title(_tier_heading(_sc))
_scL, _scR = st.columns([3, 2])
with _scL:
    theme.render_bullets(_sc["pace"], _sc["rag"])
    if not _sc["pace"] and not _sc["rag"]:
        st.info(f"The {_sc['label']} tier isn't measured in the current data yet.")
    if _sc["relative_bands"]:
        st.caption("Gauge bands = your **channel spread** (green third = best-performing "
                   "channels), not an absolute target — set `reporting.targets` in config "
                   "for client goals.")
    elif any(r["provenance"] == "industry benchmark" for r in _sc["rag"]):
        st.caption("Gauge bands are an **industry benchmark**, not a client-set target.")
with _scR:
    if _sc["grid"]:
        theme.metric_grid(f"{_sc['label']} totals", _sc["grid"], cols=2)
st.divider()


def _narrow():
    """Editorial measure: keep prose and charts on a readable column width."""
    left, _ = st.columns([7, 2])
    return left


def _stacked_monthly(scoped: pd.DataFrame, col: str, stackgroup: str) -> go.Figure:
    """Readable stacked area for a per-channel volume: MONTHLY grain (weekly x 8
    channels reads as noise over a 131-week flight) and at most 6 named channels —
    the rest fold into a quiet 'Other' band."""
    d = scoped.copy()
    d["month"] = d["date"].dt.to_period("M").dt.to_timestamp()
    per = d.groupby("channel")[col].sum().sort_values(ascending=False)
    top = [c for c in per.index if per[c] > 0][:6]
    d["bucket"] = d["channel"].where(d["channel"].isin(top), "Other")
    ts = (d.groupby(["month", "bucket"], as_index=False)[col].sum()
            .sort_values("month"))
    fig = go.Figure()
    for i, ch in enumerate(top + (["Other"] if (~d["channel"].isin(top)).any() else [])):
        g = ts[ts["bucket"] == ch]
        if not len(g) or float(g[col].sum()) <= 0:
            continue
        color = theme.GHOST if ch == "Other" else theme.channel_color(ch, i)
        fig.add_scatter(x=g["month"], y=g[col],
                        name="Other" if ch == "Other" else theme.channel_label(ch),
                        mode="lines", stackgroup=stackgroup,
                        line=dict(color=color, width=1.5))
    return fig


def _render_focus_block(spec: L.ReportSpec, wk: pd.DataFrame, kpi_label: str) -> None:
    """Dynamic 2-column block driven by the query's focus_metric."""
    fm = spec.focus_metric
    if fm is None:
        return
    scoped = wk if spec.channels is None else wk[wk["channel"].isin(spec.channels)]
    per_ch = scoped.groupby("channel")

    _labels = {
        "clicks": "Click performance",
        "impressions": "Impression performance",
        "spend": "Spend breakdown",
        "conversions": "Conversion performance",
        "roas": "Revenue & ROAS",
        "engagement": "Engagement performance",
    }
    theme.action_title(_labels.get(fm, fm.capitalize()))
    left, right = st.columns([2, 1])

    if fm == "clicks":
        with left:
            theme.plotly_chart(_stacked_monthly(scoped, "clicks", "clicks"),
                               yfmt="count", height=300)
            st.caption("Monthly clicks, top channels stacked (smaller channels fold "
                       "into Other).")
        with right:
            ctr = per_ch.agg(clicks=("clicks", "sum"),
                             impressions=("impressions", "sum")).reset_index()
            ctr["ctr"] = ctr["clicks"] / ctr["impressions"].replace(0, float("nan"))
            ctr = ctr.dropna(subset=["ctr"]).sort_values("ctr", ascending=True)
            fig = go.Figure(go.Bar(
                y=[theme.channel_label(c) for c in ctr["channel"]],
                x=ctr["ctr"], orientation="h",
                marker_color=[theme.channel_color(c, i) for i, c in enumerate(ctr["channel"])],
                text=[f"{v * 100:.2f}%" for v in ctr["ctr"]], textposition="outside"))
            theme.plotly_chart(fig, xfmt="pct", height=300, legend=False)
            st.caption("CTR = clicks / impressions by channel.")

    elif fm == "impressions":
        with left:
            theme.plotly_chart(_stacked_monthly(scoped, "impressions", "impr"),
                               yfmt="count", height=300)
            st.caption("Monthly impressions, top channels stacked (smaller channels "
                       "fold into Other).")
        with right:
            cpm = per_ch.agg(spend=("spend", "sum"),
                             impressions=("impressions", "sum")).reset_index()
            cpm["cpm"] = cpm["spend"] / cpm["impressions"].replace(0, float("nan")) * 1000
            cpm = cpm.dropna(subset=["cpm"]).sort_values("cpm", ascending=True)
            fig = go.Figure(go.Bar(
                y=[theme.channel_label(c) for c in cpm["channel"]],
                x=cpm["cpm"], orientation="h",
                marker_color=[theme.channel_color(c, i) for i, c in enumerate(cpm["channel"])],
                text=[f"${v:.2f}" for v in cpm["cpm"]], textposition="outside"))
            theme.plotly_chart(fig, xfmt="currency", height=300, legend=False)
            st.caption("CPM = cost per 1,000 impressions by channel.")

    elif fm == "spend":
        with left:
            theme.plotly_chart(_stacked_monthly(scoped, "spend", "spend"),
                               yfmt="currency", height=300)
            st.caption("Monthly spend, top channels stacked (smaller channels fold "
                       "into Other).")
        with right:
            mix = insights.spend_mix(scoped)
            fig = go.Figure(go.Pie(
                labels=[theme.channel_label(c) for c in mix["channel"]],
                values=mix["spend"], hole=0.62, sort=False,
                marker=dict(colors=[theme.channel_color(c, i)
                                    for i, c in enumerate(mix["channel"])]),
                textinfo="percent", textfont=dict(size=12)))
            fig.update_layout(annotations=[dict(text="Spend<br>mix", showarrow=False,
                              font=dict(family=theme.SANS, size=14, color=theme.INK_SOFT))])
            theme.plotly_chart(fig, height=300, legend=True)

    elif fm == "conversions":
        b_cv = insights.claims_vs_measured_insight(scoped, kpi_label)
        b_cp = insights.cost_per_outcome_insight(scoped, kpi_label)
        with left:
            if b_cv:
                per = b_cv["per_channel"]
                labels = [theme.channel_label(c) for c in per["channel"]]
                fig = go.Figure()
                fig.add_bar(x=labels, y=per["claimed"], name="Platform-claimed",
                            marker_color=theme.CLAIMED,
                            text=[f"{r:.1f}x" for r in per["ratio"]], textposition="outside")
                fig.add_bar(x=labels, y=per["measured"], name="Analytics-measured",
                            marker_color=theme.MEASURED)
                fig.update_layout(barmode="group")
                theme.plotly_chart(fig, yfmt="count", height=300)
        with right:
            if b_cp:
                per = b_cp["per_channel"].sort_values("cost_per", ascending=True)
                fig = go.Figure(go.Bar(
                    y=[theme.channel_label(c) for c in per["channel"]],
                    x=per["cost_per"], orientation="h",
                    marker_color=[theme.channel_color(c, i) for i, c in enumerate(per["channel"])],
                    text=[insights._money(v) for v in per["cost_per"]], textposition="outside"))
                fig.update_layout(showlegend=False)
                theme.plotly_chart(fig, xfmt="currency", height=300, legend=False)
                cap = ("per analytics-measured outcome" if b_cp["measured"]
                       else "per platform-claimed conversion")
                st.caption(f"Cost {cap}.")

    elif fm == "roas":
        per = per_ch.agg(spend=("spend", "sum"),
                         revenue=("platform_revenue", "sum")).reset_index()
        per["roas"] = per["revenue"] / per["spend"].replace(0, float("nan"))
        per = per.dropna(subset=["roas"]).sort_values("roas", ascending=False)
        labels = [theme.channel_label(c) for c in per["channel"]]
        with left:
            fig = go.Figure()
            fig.add_bar(x=labels, y=per["spend"], name="Spend",
                        marker_color=theme.SPEND)
            fig.add_bar(x=labels, y=per["revenue"], name="Platform revenue",
                        marker_color=theme.CLAIMED)   # platform-attributed = claimed
            fig.update_layout(barmode="group")
            theme.plotly_chart(fig, yfmt="currency", height=300)
            st.caption("Spend is graphite (money in); platform-attributed revenue is "
                       "amber (self-reported, like all claimed figures).")
        with right:
            fig = go.Figure(go.Bar(
                y=labels[::-1], x=per["roas"].tolist()[::-1], orientation="h",
                marker_color=[theme.channel_color(c, i)
                              for i, c in enumerate(per["channel"])][::-1],
                text=[f"{v:.2f}x" for v in per["roas"].tolist()[::-1]],
                textposition="outside"))
            theme.plotly_chart(fig, xfmt="count", height=300, legend=False)
            st.caption("Blended ROAS = platform-attributed revenue / spend.")

    elif fm == "engagement":
        if "sessions" in scoped.columns and scoped["sessions"].notna().any():
            with left:
                theme.plotly_chart(_stacked_monthly(scoped, "sessions", "sess"),
                                   yfmt="count", height=300)
                st.caption("Monthly sessions, top channels stacked (smaller channels "
                           "fold into Other).")
            with right:
                eng = per_ch.agg(sessions=("sessions", "sum"),
                                 engaged=("engaged_sessions", "sum")).reset_index()
                eng["eng_rate"] = eng["engaged"] / eng["sessions"].replace(0, float("nan"))
                eng = eng.dropna(subset=["eng_rate"]).sort_values("eng_rate", ascending=True)
                fig = go.Figure(go.Bar(
                    y=[theme.channel_label(c) for c in eng["channel"]],
                    x=eng["eng_rate"], orientation="h",
                    marker_color=[theme.channel_color(c, i) for i, c in enumerate(eng["channel"])],
                    text=[f"{v * 100:.1f}%" for v in eng["eng_rate"]], textposition="outside"))
                theme.plotly_chart(fig, xfmt="pct", height=300, legend=False)
                st.caption("Engagement rate = engaged sessions / total sessions "
                           "(web analytics).")
        else:
            st.info("Sessions data isn't in the processed metrics yet. "
                    "Re-run the pipeline after engagement columns are populated.")
    st.divider()


# --- quick views + their dynamic focus block (control sits right above its output) --
# ROAS/revenue only makes sense when platform revenue is tracked — otherwise the lens
# renders all-0.00x bars on a degenerate axis, so drop it. Engagement likewise needs
# sessions. "Spend mix" replaces the old "Budget" pill (there's no budget plan here).
_has_revenue = ("platform_revenue" in weekly
                and float(weekly["platform_revenue"].fillna(0).sum()) > 0)
_has_sessions = ("sessions" in weekly and weekly["sessions"].notna().any()
                 and float(weekly["sessions"].fillna(0).sum()) > 0)
_PRESET_QUERIES = {
    "Clicks":      "break down click performance",
    "Spend mix":   "where is my budget going",
    "Impressions": "show impressions by channel",
    "Conversions": "conversion breakdown",
}
if _has_revenue:
    _PRESET_QUERIES["ROAS"] = "what is the ROAS"
if _has_sessions:
    _PRESET_QUERIES["Engagement"] = "engagement performance"
theme.action_title("Quick views",
                   "Tap a lens to drop a focused breakdown in below; tap again to clear.")
_picked = st.pills("Quick views", list(_PRESET_QUERIES), selection_mode="single",
                   default=None, key="_quick_view", label_visibility="collapsed")
if _picked:
    _render_focus_block(_parse_lens_cached(_PRESET_QUERIES[_picked]), weekly,
                        kpi_label)                       # renders its own divider
else:
    st.divider()

# --- the insight blocks -------------------------------------------------------------
# Each block is a named renderer in the fixed catalog (agent/validate.py
# BLOCK_CATALOG); the report spec may select/reorder them, never add to them.
# Default order = the catalog order = the pre-spec hardcoded order.

# Woven AI commentary: the guard-passed, block-tagged sections from the sidecar.
# Each renders as a quiet "AI read" aside directly under its chart; whatever isn't
# woven (scorecard/incrementality/general) stays in the standalone section below.
_ai_sections: dict[str, list[str]] = {}
_ai_payload = None
if _ai_on:
    _ai_payload, _ai_side_note = load_active_commentary_sections(ROOT)
    for _s in (_ai_payload or {}).get("sections") or []:
        _ai_sections.setdefault(_s.get("block") or "general", []).append(_s["text"])


def _ai_aside(block: str) -> None:
    for _txt in _ai_sections.get(block, []):
        theme.ai_aside(_txt)


def _block_kpi_trend() -> None:
    b = insights.kpi_trend_insight(weekly, kpi_label)
    if not b:
        return
    with _narrow():
        theme.action_title(b["title"])
        fig = go.Figure()
        colors = {"Paid media": theme.ACCENT, insights.NONPAID_LABEL: theme.GRID}
        for name in [c for c in ("Paid media", insights.NONPAID_LABEL)
                     if c in b["series"].columns]:
            fig.add_scatter(x=b["series"].index, y=b["series"][name], name=name,
                            mode="lines", stackgroup="kpi",
                            line=dict(color=colors.get(name, theme.INK_SOFT), width=2))
        # hollow "ghost" markers on the flight's clipped edge weeks — the honesty
        # grammar (hollow = partial), so the end-dip reads as incomplete, not a fall
        if b.get("partial_weeks"):
            px = [d for d, _ in b["partial_weeks"]]
            py = [v for _, v in b["partial_weeks"]]
            fig.add_scatter(x=px, y=py, mode="markers", name="Partial week",
                            showlegend=False,
                            marker=dict(size=10, color=theme.PAPER,
                                        line=dict(color=theme.GHOST, width=2)),
                            hovertemplate="partial week (clipped): %{y:,.0f}<extra></extra>")
            theme.annotate(fig, px[-1], py[-1], "partial week", above=False)
        for x, y, text in b["annotations"]:
            theme.annotate(fig, x, y, text)
        theme.plotly_chart(fig, yfmt="count", height=340)
        theme.prose(b["narrative"])
        _ai_aside("kpi_trend")
    st.divider()


def _block_claims_vs_measured() -> None:
    b = insights.claims_vs_measured_insight(weekly, kpi_label)
    if not b:
        return
    with _narrow():
        theme.action_title(b["title"])
        per = b["per_channel"]
        labels = [theme.channel_label(c) for c in per["channel"]]
        fig = go.Figure()
        fig.add_bar(x=labels, y=per["claimed"], name="Platform-claimed",
                    marker_color=theme.CLAIMED, customdata=list(per["channel"]),
                    text=[f"{r:.1f}x" for r in per["ratio"]], textposition="outside")
        fig.add_bar(x=labels, y=per["measured"], name="Analytics-measured",
                    marker_color=theme.MEASURED, customdata=list(per["channel"]))
        fig.update_layout(barmode="group")
        filters.handle_channel_click(
            theme.plotly_chart(fig, yfmt="count", height=360,
                               select_key="sel_claims"))
        theme.prose(b["narrative"])
        _ai_aside("claims_vs_measured")
    st.divider()


def _block_cost_per_outcome() -> None:
    b = insights.cost_per_outcome_insight(weekly, kpi_label)
    if not b:
        return
    with _narrow():
        theme.action_title(b["title"])
        per = b["per_channel"].sort_values("cost_per", ascending=True)
        fig = go.Figure()
        fig.add_bar(
            y=[theme.channel_label(c) for c in per["channel"]], x=per["cost_per"],
            orientation="h", customdata=list(per["channel"]),
            marker_color=[theme.channel_color(c, i)
                          for i, c in enumerate(per["channel"])],
            text=[insights._money(v) for v in per["cost_per"]], textposition="outside")
        fig.update_layout(showlegend=False)
        filters.handle_channel_click(
            theme.plotly_chart(fig, xfmt="currency", height=60 + 52 * len(per),
                               legend=False, select_key="sel_costper"))
        cap = ("per analytics-measured outcome" if b["measured"]
               else "per platform-claimed conversion")
        st.caption(f"Cost {cap}.")
        theme.prose(b["narrative"])
        _ai_aside("cost_per_outcome")
    st.divider()


def _block_audience_callout() -> None:
    # requires history.parquet with decoded ad-level rows
    if hist is not None:
        b = insights.audience_callout_insight(hist)
        if not b:
            return
        with _narrow():
            theme.action_title(b["title"])
            per = b["per_audience"].head(6)
            labels = [f"{r['audience_type']} · {r['audience_detail']}"
                      for _, r in per.iterrows()]
            # audience TYPES get their own hues (never the reserved amber/ink — all these
            # figures are platform-claimed, so amber-for-retargeting would clash)
            _atype = {"PROSPECT": "#4E79A7", "RETARGET": "#2A9D8F"}
            colors = [_atype.get(r["audience_type"], theme.GHOST)
                      for _, r in per.iterrows()]
            fig = go.Figure(go.Bar(
                y=labels[::-1], x=per["cost_per_claimed"].tolist()[::-1],
                orientation="h", marker_color=colors[::-1],
                text=[insights._money(v) for v in per["cost_per_claimed"]][::-1],
                textposition="outside"))
            fig.update_xaxes(range=[0, float(per["cost_per_claimed"].max()) * 1.18])
            theme.plotly_chart(fig, xfmt="currency",
                               height=80 + 44 * len(per), legend=False)
            st.caption("Cost per **platform-claimed** conversion per audience, decoded "
                       "from ad-set names (slate = prospecting, teal = retargeting). "
                       "Warm retargeting audiences convert cheaper by construction — "
                       "compare within a type.")
            theme.prose(b["narrative"])
            _ai_aside("audience_callout")
        st.divider()

def _block_recruiting_pipeline() -> None:
    # CRM applicant gates (post-submission). Deliberately NOT scoped by the sidebar
    # date/channel filters: stage completions lag media by months, so slicing them to
    # the media window would misread as a collapse. Full engagement, always.
    b = insights.recruiting_pipeline_insight(_stages_df)
    if not b:
        return
    with _narrow():
        theme.action_title(b["title"],
                           "Full engagement to date — not affected by the sidebar "
                           "filters (stage completions lag media by months).")
        df = b["stages"]
        labels = list(df["label"])
        text = [f"{v:,.0f}" + (f"  ·  {r * 100:.0f}% of prior gate" if r == r else "")
                for v, r in zip(df["value"], df["step_rate"])]
        fig = go.Figure(go.Bar(
            y=labels[::-1], x=df["value"].tolist()[::-1], orientation="h",
            marker_color=theme.MEASURED, text=text[::-1], textposition="outside"))
        fig.update_xaxes(range=[0, float(df["value"].max()) * 1.3])
        theme.plotly_chart(fig, xfmt="count", height=80 + 46 * len(df), legend=False)
        st.caption(b["censor_note"])
        theme.prose(b["narrative"])
        _ai_aside("recruiting_pipeline")
    st.divider()


def _block_pacing() -> None:
    b = insights.pacing_insight(weekly, budget_cfg)
    if not b:
        return
    with _narrow():
        theme.action_title(b["title"])
        left, right = st.columns([5, 3])
        with left:
            cum = b["cumulative"]
            fig = go.Figure()
            fig.add_scatter(x=cum.index, y=cum.values, name="Cumulative spend",
                            mode="lines", line=dict(color=theme.SPEND, width=2.5),
                            fill="tozeroy", fillcolor="rgba(122,114,102,0.10)")
            if b["budget"]:
                plan_x = [cum.index.min(), cum.index.min()
                          + pd.Timedelta(weeks=b["budget"]["flight_weeks"])]
                fig.add_scatter(x=plan_x, y=[0, b["budget"]["total"]], name="Plan",
                                mode="lines",
                                line=dict(color=theme.INK_SOFT, width=1.5, dash="dot"))
            theme.plotly_chart(fig, yfmt="currency", height=320,
                               legend=bool(b["budget"]))
        with right:
            mix = insights.spend_mix(weekly)
            fig = go.Figure(go.Pie(
                labels=[theme.channel_label(c) for c in mix["channel"]],
                values=mix["spend"], hole=0.62, sort=False,
                customdata=list(mix["channel"]),
                marker=dict(colors=[theme.channel_color(c, i)
                                    for i, c in enumerate(mix["channel"])]),
                textinfo="percent", textfont=dict(size=12)))
            fig.update_layout(
                annotations=[dict(text="Spend<br>mix", showarrow=False,
                                  font=dict(family=theme.SANS, size=14,
                                            color=theme.INK_SOFT))])
            filters.handle_channel_click(
                theme.plotly_chart(fig, height=320, legend=True,
                                   select_key="sel_overview_mix"))
        theme.prose(b["narrative"])
        _ai_aside("pacing")


# --- render the blocks: spec selection/order, else the full catalog in default order
_BLOCK_RENDERERS = {
    "kpi_trend": _block_kpi_trend,
    "claims_vs_measured": _block_claims_vs_measured,
    "cost_per_outcome": _block_cost_per_outcome,
    "audience_callout": _block_audience_callout,
    "recruiting_pipeline": _block_recruiting_pipeline,
    "pacing": _block_pacing,
}
assert set(_BLOCK_RENDERERS) == set(BLOCK_CATALOG), \
    "dashboard block renderers out of sync with agent BLOCK_CATALOG"
for _name in (spec.get("blocks") or BLOCK_CATALOG):
    _BLOCK_RENDERERS[_name]()

# --- AI commentary (A2): off by default, clearly stamped, number-guarded -----------
# With a block-tagged sidecar, the per-block paragraphs are already WOVEN under their
# charts above — the standalone section carries the lede, any un-woven sections
# (scorecard / incrementality / general), and the recommendations. Without a sidecar
# (older artifact), the full markdown renders here as before.
if _ai_on:
    with _narrow():
        st.divider()
        if _ai_payload:
            theme.action_title("AI summary & recommendations", STAMP)
            _rendered = set(_BLOCK_RENDERERS)
            _left_md = [_ai_payload.get("lede", "")]
            _left_md += [f"## {s['title']}\n\n{s['text']}"
                         for s in _ai_payload.get("sections") or []
                         if (s.get("block") or "general") not in _rendered]
            if _ai_payload.get("recommendations_md"):
                _left_md.append("## Recommendations\n\n"
                                + _ai_payload["recommendations_md"])
            theme.ai_block("\n\n".join(p for p in _left_md if p))
            st.caption("The per-chart “AI read” asides above come from the same "
                       "artifact. Every number was checked against the computed data "
                       "before publication; recommendations come only from the "
                       "deterministically-eligible menu.")
        else:
            _ai_body, _ai_note = load_active_commentary(ROOT)
            theme.action_title("AI commentary", STAMP)
            if _ai_body:
                theme.ai_block(_ai_body)
                st.caption("Every number above was checked against the computed data "
                           "before publication; recommendations come only from the "
                           "deterministically-eligible menu.")
            else:
                st.info(_ai_note or "No AI commentary yet — run "
                        "`python scripts/advise.py --commentary`.")

# --- external-context aside (DEFERRED: hidden until curated notes exist) -----------
notes = insights.macro_context(cfg)
if notes:
    with _narrow():
        st.divider()
        theme.action_title("External context",
                           "Curated notes — not generated, not modeled.")
        for note in notes:
            st.markdown(f"- {note}")

# --- analyst notes: methodology + the agent's watch flags (pre-client review) -------
# Relocated from the masthead — analyst matter, kept below the fold and collapsed so a
# CMO reads the numbers first but a reviewer still has the caveats before client sign-off.
if _watch_flags or spec:
    with _narrow():
        st.divider()
        with st.expander("Analyst notes — methodology & watch flags (pre-client review)"):
            if spec:
                st.caption("Layout, labels and gauge bands were arranged by the "
                           "report-spec agent (`outputs/report_spec.json`); every number "
                           "stays deterministic and explicit config keys override the spec.")
            for flag in (_watch_flags or []):
                st.markdown(f"- {flag}")

st.divider()
st.caption("Drill down: **Explore** in the top nav has the KPI pyramid, funnel, "
           "free-text lens and per-channel tables. Commentary and data-quality "
           "reports are written to `outputs/` by the pipeline.")
