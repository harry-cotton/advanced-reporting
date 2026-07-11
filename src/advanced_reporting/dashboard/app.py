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


weekly_all = _load_weekly(str(metrics_f), metrics_f.stat().st_mtime)
hist_all = _load_hist(str(history_f), history_f.stat().st_mtime) if history_f.exists() else None
cfg = load_config()
rep = cfg.get("reporting", {}) or {}
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
st.caption(f"{lo:%d %b %Y} – {hi:%d %b %Y} · {n_paid} paid channels · every number "
           "below is computed from the weekly tables — no generated commentary. "
           "Click a channel in any bar/donut to focus the whole dashboard on it.")
if spec_note:
    st.caption(f"⚠ {spec_note}")
if spec:
    st.caption("Layout, labels and gauge bands arranged by the report-spec agent "
               "(`outputs/report_spec.json`) — every number stays deterministic; "
               "explicit config keys override the spec.")
    if spec.get("watch_flags"):
        with st.expander("Agent watch flags — AI-selected from computed evidence, "
                         "review before client use"):
            for flag in spec["watch_flags"]:
                st.markdown(f"- {flag}")
filters.focus_chip()

# --- executive tile row ------------------------------------------------------------
tiles = insights.headline_tiles(weekly, kpi_label)
cols = st.columns(len(tiles))
for col, t in zip(cols, tiles):
    with col:
        theme.metric_card(t["label"], t["value"], delta=t["delta"],
                          delta_color=t["delta_color"], help=t.get("help"))
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
# targets: spec fills the gaps, explicit config wins per metric key
_targets = {**(spec.get("targets") or {}), **(rep.get("targets") or {})}
_sc = insights.tier_scorecard(weekly, _tier, targets=_targets, kpi_label=kpi_label)
theme.action_title(f"{_sc['label']} — pacing and efficiency against goal")
_scL, _scR = st.columns([3, 2])
with _scL:
    theme.render_bullets(_sc["pace"], _sc["rag"])
    if not _sc["pace"] and not _sc["rag"]:
        st.info(f"The {_sc['label']} tier isn't measured in the current data yet.")
    if _sc["relative_bands"]:
        st.caption("Gauge bands = your **channel spread** (green third = best-performing "
                   "channels); set `reporting.targets` in config for absolute goals.")
with _scR:
    if _sc["grid"]:
        theme.metric_grid(f"{_sc['label']} totals", _sc["grid"], cols=2)
st.divider()


def _narrow():
    """Editorial measure: keep prose and charts on a readable column width."""
    left, _ = st.columns([7, 2])
    return left


def _render_focus_block(spec: L.ReportSpec, wk: pd.DataFrame, kpi_label: str) -> None:
    """Dynamic 2-column block driven by the query's focus_metric."""
    fm = spec.focus_metric
    if fm is None:
        return
    scoped = wk if spec.channels is None else wk[wk["channel"].isin(spec.channels)]
    channels = insights._paid_channels(scoped)
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
            ts = scoped.groupby(["date", "channel"], as_index=False)["clicks"].sum().sort_values("date")
            fig = go.Figure()
            for i, ch in enumerate(channels):
                g = ts[ts["channel"] == ch]
                fig.add_scatter(x=g["date"], y=g["clicks"], name=theme.channel_label(ch),
                                mode="lines", stackgroup="clicks",
                                line=dict(color=theme.channel_color(ch, i), width=2))
            theme.plotly_chart(fig, yfmt="count", height=300)
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
            ts = scoped.groupby(["date", "channel"], as_index=False)["impressions"].sum().sort_values("date")
            fig = go.Figure()
            for i, ch in enumerate(channels):
                g = ts[ts["channel"] == ch]
                fig.add_scatter(x=g["date"], y=g["impressions"], name=theme.channel_label(ch),
                                mode="lines", stackgroup="impr",
                                line=dict(color=theme.channel_color(ch, i), width=2))
            theme.plotly_chart(fig, yfmt="count", height=300)
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
            ts = scoped.groupby(["date", "channel"], as_index=False)["spend"].sum().sort_values("date")
            fig = go.Figure()
            for i, ch in enumerate(channels):
                g = ts[ts["channel"] == ch]
                fig.add_scatter(x=g["date"], y=g["spend"], name=theme.channel_label(ch),
                                mode="lines", stackgroup="spend",
                                line=dict(color=theme.channel_color(ch, i), width=2))
            theme.plotly_chart(fig, yfmt="currency", height=300)
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
            theme.plotly_chart(fig, height=300, legend=False)

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
                        marker_color=theme.CLAIMED)
            fig.add_bar(x=labels, y=per["revenue"], name="Platform revenue",
                        marker_color=theme.MEASURED)
            fig.update_layout(barmode="group")
            theme.plotly_chart(fig, yfmt="currency", height=300)
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
                ts = scoped.groupby(["date", "channel"], as_index=False)["sessions"].sum().sort_values("date")
                fig = go.Figure()
                for i, ch in enumerate(channels):
                    g = ts[ts["channel"] == ch]
                    fig.add_scatter(x=g["date"], y=g["sessions"], name=theme.channel_label(ch),
                                    mode="lines", stackgroup="sess",
                                    line=dict(color=theme.channel_color(ch, i), width=2))
                theme.plotly_chart(fig, yfmt="count", height=300)
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
                st.caption("Engagement rate = engaged sessions / total sessions (GA4).")
        else:
            st.info("Sessions data isn't in the processed metrics yet. "
                    "Re-run the pipeline after GA4 engagement columns are populated.")
    st.divider()


# --- quick views + their dynamic focus block (control sits right above its output) --
_PRESET_QUERIES = {
    "Clicks":      "break down click performance",
    "Budget":      "where is my budget going",
    "ROAS":        "what is the ROAS",
    "Impressions": "show impressions by channel",
    "Conversions": "conversion breakdown",
    "Engagement":  "engagement performance",
}
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
        for x, y, text in b["annotations"]:
            theme.annotate(fig, x, y, text)
        theme.plotly_chart(fig, yfmt="count", height=340)
        theme.prose(b["narrative"])
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
            colors = [theme.ACCENT if r["audience_type"] == "PROSPECT" else theme.CLAIMED
                      for _, r in per.iterrows()]
            fig = go.Figure(go.Bar(
                y=labels[::-1], x=per["cost_per_claimed"].tolist()[::-1],
                orientation="h", marker_color=colors[::-1],
                text=[insights._money(v) for v in per["cost_per_claimed"]][::-1],
                textposition="outside"))
            theme.plotly_chart(fig, xfmt="currency",
                               height=80 + 44 * len(per), legend=False)
            st.caption("Platform-claimed conversions per audience, "
                       "decoded from ad-set names. Blue = prospecting, "
                       "amber = retargeting.")
            theme.prose(b["narrative"])
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
                            mode="lines", line=dict(color=theme.ACCENT, width=2.5),
                            fill="tozeroy", fillcolor="rgba(31,78,121,0.08)")
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


# --- render the blocks: spec selection/order, else the full catalog in default order
_BLOCK_RENDERERS = {
    "kpi_trend": _block_kpi_trend,
    "claims_vs_measured": _block_claims_vs_measured,
    "cost_per_outcome": _block_cost_per_outcome,
    "audience_callout": _block_audience_callout,
    "pacing": _block_pacing,
}
assert set(_BLOCK_RENDERERS) == set(BLOCK_CATALOG), \
    "dashboard block renderers out of sync with agent BLOCK_CATALOG"
for _name in (spec.get("blocks") or BLOCK_CATALOG):
    _BLOCK_RENDERERS[_name]()

# --- external-context aside (DEFERRED: hidden until curated notes exist) -----------
notes = insights.macro_context(cfg)
if notes:
    with _narrow():
        st.divider()
        theme.action_title("External context",
                           "Curated notes — not generated, not modeled.")
        for note in notes:
            st.markdown(f"- {note}")

st.divider()
st.caption("Drill down: **Explore** in the sidebar has the KPI pyramid, funnel, "
           "free-text lens and per-channel tables. Commentary and data-quality "
           "reports are written to `outputs/` by the pipeline.")
