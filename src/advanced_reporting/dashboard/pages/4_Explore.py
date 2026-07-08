"""Explore — the interactive drill-down dashboard (KPI pyramid, funnel, lens, table).

This is the pre-redesign dashboard, kept as a sub-page: the landing page (app.py) is
the editorial narrative Overview; dense interactive exploration lives below the fold
here. R3 splits this further into Channels / Audiences / Data quality pages.
"""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
from advanced_reporting.dashboard import filters, insights, theme  # noqa: E402
from advanced_reporting.reporting import metrics as M  # noqa: E402
from advanced_reporting.reporting import lens as L  # noqa: E402
from advanced_reporting.utils import load_config  # noqa: E402

st.set_page_config(page_title="Advanced Reporting — Explore", layout="wide")
theme.inject_css()
theme.nav_bar()
st.title("Explore")
st.caption("KPI pyramid, funnel and free-text lens over the weekly metrics — "
           "filter in the sidebar.")

metrics_f = ROOT / "data" / "processed" / "channel_weekly_metrics.csv"
summary_f = ROOT / "outputs" / "channel_summary.csv"

if not metrics_f.exists():
    st.warning("No processed data yet. Run `python scripts/run_pipeline.py` first.")
    st.stop()


@st.cache_data
def _load_metrics(path: str, mtime: float) -> pd.DataFrame:
    return pd.read_csv(path, parse_dates=["date"])


@st.cache_data
def _parse_lens_cached(text: str, use_llm: bool):
    # cached per (text, toggle): Streamlit reruns this script on EVERY widget
    # interaction, and an uncached parse re-fired a live LLM call each time —
    # latency on every click, plus the parse could change between reruns
    return L.parse_lens(text, use_llm=use_llm)


m = _load_metrics(str(metrics_f), metrics_f.stat().st_mtime)
has_engagement = "sessions" in m.columns
_rep = (load_config().get("reporting", {}) or {})
_targets = _rep.get("targets") or {}
kpi_label = _rep.get("kpi_label", "key events")

# --- sidebar: global filters + goal lens ---
dr, chsel = filters.sidebar_filters(
    m["channel"].unique(), m["date"].min().date(), m["date"].max().date())

goals_cfg = M.load_campaign_goals()
goal_tiers = goals_cfg.get("goal_primary_tier") or {
    "awareness": "reach", "consideration": "intent", "conversion": "outcome"}
goal_list = list(goal_tiers.keys())
default_goal = goals_cfg.get("default_goal", goal_list[-1] if goal_list else "conversion")
goal = st.sidebar.pills(
    "Campaign goal (report lens)", goal_list, selection_mode="single",
    default=default_goal, key="_explore_goal") or default_goal
primary = M.primary_tier(goal, goals_cfg)

lens_text = st.sidebar.text_input("Report lens (free text)",
                                  placeholder="e.g. this is an awareness campaign")
use_llm = st.sidebar.toggle(
    "Parse lens with LLM", value=False,
    help="Off = deterministic keyword parser (no network). On = one Claude call per "
         "unique lens text; needs ANTHROPIC_API_KEY.")
lens_spec = None
if lens_text.strip():
    lens_spec = _parse_lens_cached(lens_text.strip(), use_llm)
    goal, primary = lens_spec.goal, lens_spec.primary_tier
    st.sidebar.caption(f"Lens overrides the goal selector → **{goal}**"
                       + (f", channels: {', '.join(lens_spec.channels)}"
                          if lens_spec.channels else ""))

f = filters.apply(m, dr, chsel)
if lens_spec is not None and lens_spec.channels:
    f = f[f["channel"].isin(lens_spec.channels)]
if f.empty:
    st.info("No rows for the current filters.")
    st.stop()

# --- headline tiles ---
spend, rev, conv = f["spend"].sum(), f["platform_revenue"].sum(), f["conversions"].sum()
roas_blended = rev / spend if spend else 0.0
c1, c2, c3, c4 = st.columns(4)
with c1:
    theme.metric_card("Spend", insights._money(float(spend)))
with c2:
    theme.metric_card("Attributed revenue", insights._money(float(rev)))
with c3:
    theme.metric_card("Blended ROAS", f"{roas_blended:.2f}x")
with c4:
    theme.metric_card("Avg CPA", M.format_value(spend / conv, "currency") if conv else "—")
st.divider()

if not has_engagement:
    st.caption("Intent (engagement) tier isn't in the data yet — re-run "
               "`python scripts/run_pipeline.py` after the Phase-2 engagement update to "
               "populate sessions/engagement. Reach + outcome tiers show below.")

# --- KPI pyramid (apex -> base) as goal gauges, primary tier flagged by the lens ---
st.subheader("KPI pyramid")
st.caption(f"Goal lens: **{goal}** → primary tier: **{primary}**. Efficiency and quality "
           "metrics are graded (green/amber/red) against your channel spread — or "
           "`reporting.targets` when set; volumes show as totals.")
TIER_TITLE = {"outcome": "Outcome / action", "intent": "Intent / engagement",
              "reach": "Reach / awareness"}
_any_relative = False
for tier in ["outcome", "intent", "reach"]:
    sc = insights.tier_scorecard(f, tier, targets=_targets, kpi_label=kpi_label)
    star = " ⭐ primary" if tier == primary else ""
    theme.action_title(f"{TIER_TITLE.get(tier, tier)}{star}")
    if not sc["pace"] and not sc["rag"] and len(sc["grid"]) <= 1:
        st.caption("Not measured in the current data yet.")
        st.divider()
        continue
    _l, _r = st.columns([3, 2])
    with _l:
        theme.render_bullets(sc["pace"], sc["rag"])
        if not sc["pace"] and not sc["rag"]:
            st.caption("No graded metrics for this tier — see totals.")
    with _r:
        if sc["grid"]:
            theme.metric_grid(f"{TIER_TITLE.get(tier, tier)} totals", sc["grid"], cols=2)
    _any_relative = _any_relative or sc["relative_bands"]
    st.divider()
if _any_relative:
    st.caption("Gauge bands = your channel spread (green third = best-performing "
               "channels); set `reporting.targets` in config for absolute goals.")

if lens_spec is not None:
    st.subheader("Lens report")
    st.markdown(L.render_narrative(lens_spec, f))

# --- funnel pass-through / drop-off ---
st.subheader("Funnel & drop-off")
recs = M.funnel(f).to_dict("records")
if recs:
    fcols = st.columns(len(recs))
    for col, r in zip(fcols, recs):
        sr = r["step_rate"]
        delta = None if pd.isna(sr) else f"{sr*100:.1f}% pass-through"
        with col:
            theme.metric_card(r["label"], M.format_value(r["value"], "count"),
                               delta=delta, delta_color="off")
    st.caption("Volume at each stage with pass-through from the prior stage "
               "(impressions → clicks → sessions → engaged → conversions).")
else:
    st.caption("No funnel volumes for the current filter.")

# --- standard channel performance table ---
st.subheader("Channel performance")
base = ["spend", "impressions", "clicks", "conversions", "platform_revenue"]
eng = [c for c in ["sessions", "engaged_sessions", "video_views"] if c in f.columns]
agg = f.groupby("channel")[base + eng].sum().reset_index()
agg["CTR %"] = (agg["clicks"] / agg["impressions"] * 100).round(2)
if "sessions" in agg.columns and "engaged_sessions" in agg.columns:
    agg["Eng. rate %"] = (agg["engaged_sessions"] / agg["sessions"] * 100).round(2)
agg["CPA"] = (agg["spend"] / agg["conversions"]).round(0)
agg["ROAS"] = (agg["platform_revenue"] / agg["spend"]).round(2)
st.dataframe(agg, use_container_width=True)

# --- monthly trends as bar+line combos (volume + efficiency) ---
st.subheader("Trends by month")
_mo = f.copy()
_mo["month"] = _mo["date"].dt.to_period("M").dt.to_timestamp()
mo = (_mo.groupby("month")
         .agg(spend=("spend", "sum"), impressions=("impressions", "sum"),
              clicks=("clicks", "sum"), conversions=("conversions", "sum"))
         .reset_index().sort_values("month"))
mo["cpm"] = mo["spend"] / mo["impressions"].replace(0, float("nan")) * 1000
mo["cpa"] = mo["spend"] / mo["conversions"].replace(0, float("nan"))
_c1, _c2 = st.columns(2)
with _c1:
    theme.action_title("Impressions & CPM by month")
    _bc, _lc = theme.COMBO_PAIRS["violet_gold"]
    theme.combo(mo["month"], mo["impressions"], mo["cpm"], bar_name="Impressions",
                line_name="CPM", bar_fmt="count", line_fmt="currency", y2_title="CPM",
                bar_color=_bc, line_color=_lc, height=320)
with _c2:
    theme.action_title("Spend & CPA by month")
    _bc, _lc = theme.COMBO_PAIRS["green_terra"]
    theme.combo(mo["month"], mo["spend"], mo["cpa"], bar_name="Spend", line_name="CPA",
                bar_fmt="currency", line_fmt="currency", y2_title="CPA",
                bar_color=_bc, line_color=_lc, height=320)
st.caption("CPA uses platform-claimed conversions.")

# --- MMM summary (if a model run is available) ---
if summary_f.exists():
    st.subheader("MMM — estimated contribution & ROI (90% intervals)")
    st.dataframe(pd.read_csv(summary_f), use_container_width=True)
    st.caption("MMM figures are modeled, uncertainty-bound estimates — validate with "
               "experiments before reallocating budget.")
