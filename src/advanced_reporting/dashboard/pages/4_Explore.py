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
from advanced_reporting.dashboard import insights, theme  # noqa: E402
from advanced_reporting.reporting import metrics as M  # noqa: E402
from advanced_reporting.reporting import lens as L  # noqa: E402

st.set_page_config(page_title="Explore — Advanced Reporting", layout="wide")
theme.inject_css()
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

# --- sidebar: filters + goal lens ---
channels = sorted(m["channel"].unique())
sel = st.sidebar.multiselect("Channels", channels, default=channels)
dr = st.sidebar.date_input("Date range", (m["date"].min(), m["date"].max()))

goals_cfg = M.load_campaign_goals()
goal_tiers = goals_cfg.get("goal_primary_tier") or {
    "awareness": "reach", "consideration": "intent", "conversion": "outcome"}
goal_list = list(goal_tiers.keys())
default_goal = goals_cfg.get("default_goal", goal_list[-1] if goal_list else "conversion")
goal = st.sidebar.selectbox(
    "Campaign goal (report lens)", goal_list,
    index=goal_list.index(default_goal) if default_goal in goal_list else 0)
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

f = m[m["channel"].isin(sel)]
if lens_spec is not None and lens_spec.channels:
    f = f[f["channel"].isin(lens_spec.channels)]
if isinstance(dr, tuple) and len(dr) == 2:
    f = f[(f["date"] >= pd.Timestamp(dr[0])) & (f["date"] <= pd.Timestamp(dr[1]))]
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

# --- KPI pyramid (apex -> base), primary tier highlighted by the goal lens ---
st.subheader("KPI pyramid")
st.caption(f"Goal lens: **{goal}** → primary tier: **{primary}**. "
           "Values are aggregate ratios over the current filter.")
pyr = M.pyramid(f)
TIER_TITLE = {"outcome": "Outcome / action", "intent": "Intent / engagement",
              "reach": "Reach / awareness"}
for tier in ["outcome", "intent", "reach"]:
    rows = pyr.get(tier, [])
    title = TIER_TITLE.get(tier, tier)
    if tier == primary:
        st.markdown(f"### {title} ⭐ _primary_")
    else:
        st.markdown(f"#### {title}")
    cols = st.columns(max(len(rows), 1))
    for col, r in zip(cols, rows):
        col.metric(r["label"], M.format_value(r["value"], r["format"]))
    st.divider()

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

# --- spend over time (house-style plotly; per-channel so the mix is visible) ---
st.subheader("Spend over time")
ts = (f.groupby(["date", "channel"], as_index=False)["spend"].sum()
        .sort_values("date"))
fig = go.Figure()
for i, ch in enumerate(sorted(ts["channel"].unique())):
    g = ts[ts["channel"] == ch]
    fig.add_scatter(x=g["date"], y=g["spend"], name=theme.channel_label(ch),
                    mode="lines", line=dict(color=theme.channel_color(ch, i), width=2),
                    stackgroup="spend")
theme.plotly_chart(fig, yfmt="currency")

# --- MMM summary (if a model run is available) ---
if summary_f.exists():
    st.subheader("MMM — estimated contribution & ROI (90% intervals)")
    st.dataframe(pd.read_csv(summary_f), use_container_width=True)
    st.caption("MMM figures are modeled, uncertainty-bound estimates — validate with "
               "experiments before reallocating budget.")
