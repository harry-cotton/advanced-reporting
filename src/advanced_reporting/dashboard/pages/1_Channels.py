"""Channels — the drill-down explorer (redesign R3).

Per-channel trends, the efficiency view, campaign tables and the nested
channel → campaign → audience → creative breakdown with CSV download.
GA4 key events exist at campaign grain only; below that, conversions are
platform-claimed and labeled so.
"""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
from advanced_reporting.agent import load_active_spec  # noqa: E402
from advanced_reporting.dashboard import drilldown, filters, insights, theme  # noqa: E402
from advanced_reporting.utils import load_config  # noqa: E402

st.set_page_config(page_title="Advanced Reporting — Channels", layout="wide")
theme.inject_css()
theme.nav_bar()

_cfg = load_config()
_rep = (_cfg.get("reporting") or {})
_spec, _ = load_active_spec(ROOT)
KPI = _rep.get("kpi_label") or _spec.get("kpi_label") or "key events"
_KPIS = KPI[:-1] if KPI.endswith("s") else KPI          # singular ("application start")

st.title("Channels")
st.caption(f"Trends, efficiency and the campaign → audience → creative breakdown. "
           f"**{KPI.capitalize()}** are analytics-measured at campaign grain; everything "
           "below that is **platform-claimed**.")

metrics_f = ROOT / "data" / "processed" / "channel_weekly_metrics.csv"
history_f = ROOT / "data" / "processed" / "history.parquet"
if not metrics_f.exists() or not history_f.exists():
    st.warning("No processed data yet. Run `python scripts/ingest.py --inbox` (or "
               "`--source synthetic`) and `python scripts/run_pipeline.py` first.")
    st.stop()


@st.cache_data
def _load(path: str, mtime: float) -> pd.DataFrame:
    if path.endswith(".parquet"):
        return pd.read_parquet(path)
    return pd.read_csv(path, parse_dates=["date"])


weekly = _load(str(metrics_f), metrics_f.stat().st_mtime)
hist = _load(str(history_f), history_f.stat().st_mtime)

_dr, _chsel = filters.sidebar_filters(
    weekly["channel"].unique(),
    weekly["date"].min().date(), weekly["date"].max().date())
weekly = filters.apply(weekly, _dr, _chsel)
hist = filters.apply(hist, _dr, _chsel)
filters.focus_chip()
if weekly.empty:
    st.info("No rows for the current filter — widen the date range or channel selection.")
    st.stop()

# --- metric tile row ----------------------------------------------------------------
_measured = "key_events" in weekly.columns and weekly["key_events"].notna().any()
_out_col = "key_events" if _measured else "conversions"
_out_label = KPI.capitalize() if _measured else "Claimed conv."


def _compact(n: float) -> str:
    """House compact form for big counts (4,378,790 → 4.4M) — matches the $35.2k style."""
    n = float(n)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(n) >= div:
            return f"{n / div:.1f}{suf}"
    return f"{n:,.0f}"


_tcols = st.columns(4)
with _tcols[0]:
    theme.metric_card("Spend", insights._money(float(weekly["spend"].sum())))
with _tcols[1]:
    theme.metric_card("Impressions", _compact(weekly["impressions"].sum()))
with _tcols[2]:
    theme.metric_card("Clicks", _compact(weekly["clicks"].sum()))
with _tcols[3]:
    theme.metric_card(_out_label, f"{float(weekly[_out_col].sum()):,.0f}")
st.divider()

# --- efficiency at a glance (reach-tier gauges for the current selection) ------------
_sc = insights.tier_scorecard(weekly, "reach", targets=_rep.get("targets") or {},
                              config_target_keys=set(_rep.get("targets") or {}))
if _sc["rag"]:
    theme.action_title(
        "Efficiency at a glance",
        "CPM / CPC / CTR for the selected channels, graded against the channel spread "
        "(green third = your best channels), not an absolute target — set "
        "reporting.targets in config for client goals.")
    for _col, _r in zip(st.columns(len(_sc["rag"])), _sc["rag"]):
        with _col:
            theme.render_bullets(rag=[_r])
    st.divider()

# --- spend & CPM by channel (readable bar+line combo) + mix -------------------------
per_spend = weekly.groupby("channel")["spend"].sum().sort_values(ascending=False)
ch_agg = (weekly.groupby("channel")
          .agg(spend=("spend", "sum"), impressions=("impressions", "sum"))
          .reset_index())
ch_agg = ch_agg[ch_agg["spend"] > 0].sort_values("spend", ascending=False)
ch_agg["cpm"] = ch_agg["spend"] / ch_agg["impressions"].replace(0, float("nan")) * 1000
top_ch = ch_agg.iloc[0]["channel"]
top_share = ch_agg.iloc[0]["spend"] / ch_agg["spend"].sum()
theme.action_title(
    f"{theme.channel_label(top_ch)} takes {top_share * 100:.0f}% of paid spend",
    "Bars = spend by channel, line = CPM (cost per 1,000 impressions). "
    "Click a bar or slice to focus every chart on that channel.")
_left, _right = st.columns([2, 1])
with _left:
    # mono combo: graphite spend bars + near-ink CPM line (theme defaults) — no amber,
    # which now strictly means "platform-claimed"
    _ev = theme.combo([theme.channel_label(c) for c in ch_agg["channel"]],
                      ch_agg["spend"], ch_agg["cpm"], bar_name="Spend", line_name="CPM",
                      bar_fmt="currency", line_fmt="currency", y2_title="CPM",
                      height=360,
                      customdata=list(ch_agg["channel"]), select_key="sel_spend_cpm")
    filters.handle_channel_click(_ev)
with _right:
    mix = insights.spend_mix(weekly)
    fig = go.Figure(go.Pie(
        labels=[theme.channel_label(c) for c in mix["channel"]],
        values=mix["spend"], hole=0.62, sort=False,
        customdata=list(mix["channel"]),
        marker=dict(colors=[theme.channel_color(c, i)
                            for i, c in enumerate(mix["channel"])]),
        textinfo="percent", textfont=dict(size=12)))
    fig.update_layout(annotations=[dict(text="Spend<br>mix", showarrow=False,
                      font=dict(family=theme.SANS, size=14, color=theme.INK_SOFT))])
    filters.handle_channel_click(
        theme.plotly_chart(fig, height=360, legend=True, select_key="sel_mix"))

# --- spend & CPC by month (combo trend, readable in place of the old area) ----------
_mo = weekly.copy()
_mo["month"] = _mo["date"].dt.to_period("M").dt.to_timestamp()
mo = (_mo.groupby("month").agg(spend=("spend", "sum"), clicks=("clicks", "sum"))
         .reset_index().sort_values("month"))
mo["cpc"] = mo["spend"] / mo["clicks"].replace(0, float("nan"))
theme.action_title("Spend and CPC by month",
                   "Monthly spend (bars) with cost-per-click overlaid (line).")
# CPC is sub-dollar (~$0.50), so the line axis needs 2 decimals — "$,.0f" rendered it as
# a flat "$0 $0 $1". Mono combo (graphite bar + ink line) via the theme defaults.
theme.combo(mo["month"], mo["spend"], mo["cpc"], bar_name="Spend", line_name="CPC",
            bar_fmt="currency", line_fmt="$,.2f", y2_title="CPC", height=320)

# --- efficiency view (paired bars: rank on the left, spend context on the right) ------
eff = insights.cost_per_outcome_insight(weekly)
if eff:
    theme.action_title(eff["title"])
    per = eff["per_channel"].sort_values("cost_per")     # cheapest first, both panels
    fig = theme.paired_bars_fig(
        [theme.channel_label(c) for c in per["channel"]],
        per["cost_per"], per["spend"],
        name1=f"Cost / {eff['outcome_label']}", name2="Spend",
        fmt1="currency", fmt2="currency",
        colors1=[theme.channel_color(c, i) for i, c in enumerate(per["channel"])],
        customdata=list(per["channel"]))
    filters.handle_channel_click(
        theme.plotly_chart(fig, height=120 + 52 * len(per), legend=False,
                           select_key="sel_efficiency"))
    st.caption("Cheapest first; the right panel shows how much budget sits at each "
               "price. "
               + ("Outcomes are analytics-measured." if eff["measured"]
                  else "Outcomes are platform-claimed — no analytics series yet."))

# --- campaign table ------------------------------------------------------------------
theme.action_title("Campaigns, ranked by spend",
                   f"{KPI.capitalize()} + cost/{_KPIS} are analytics-measured at this "
                   "grain; conversions are platform-claimed.")
camp_full = drilldown.campaign_table(hist)
camp = camp_full.copy()
camp["channel"] = camp["channel"].map(theme.channel_label)
# surface the DECISION columns (outcome + its cost) right after spend, not off-screen right
_order = ["channel", "campaign", "spend", "key_events", "cost_per_key_event",
          "conversions", "cost_per_claimed", "impressions", "clicks"]
camp = camp[[c for c in _order if c in camp.columns]]
_TOPN = 20
_shown = camp.sort_values("spend", ascending=False).head(_TOPN)
st.dataframe(
    _shown, use_container_width=True, hide_index=True,
    column_config={
        "channel": st.column_config.TextColumn("Channel"),
        "campaign": st.column_config.TextColumn("Campaign", width="medium"),
        "spend": st.column_config.NumberColumn("Spend", format="$%,.0f"),
        "key_events": st.column_config.NumberColumn(KPI.capitalize(), format="%,.0f"),
        "cost_per_key_event": st.column_config.NumberColumn(f"Cost/{_KPIS}",
                                                            format="$%,.2f"),
        "conversions": st.column_config.NumberColumn("Claimed conv.", format="%,.0f"),
        "cost_per_claimed": st.column_config.NumberColumn("Cost/claimed",
                                                          format="$%,.2f"),
        "impressions": st.column_config.NumberColumn("Impr.", format="%,.0f"),
        "clicks": st.column_config.NumberColumn("Clicks", format="%,.0f"),
    })
if len(camp) > _TOPN:
    st.caption(f"Showing the top {_TOPN} campaigns by spend of {len(camp):,}; "
               "the full table is in the CSV.")
st.download_button("Download campaign table (CSV)",
                   camp_full.to_csv(index=False).encode("utf-8"),
                   "campaigns.csv", "text/csv")

# --- nested breakdown ----------------------------------------------------------------
theme.action_title("Inside each channel: campaign → audience → creative",
                   "Ad-set/ad-group grain with the decoded naming fields. Conversions "
                   "at this grain are platform-claimed ONLY.")
ag = drilldown.ad_group_table(hist)
if ag.empty:
    st.caption("No ad-level data for the current selection — ad-set/ad-group exports "
               "(dropped in `data/inbox/` and re-ingested) unlock the audience → creative "
               "breakdown and the naming-convention decode.")
else:
    ag_cols = {
        "campaign": st.column_config.TextColumn("Campaign", width="medium"),
        "ad_group": st.column_config.TextColumn("Ad set / ad group", width="large"),
        "audience_type": st.column_config.TextColumn("Audience"),
        "audience_detail": st.column_config.TextColumn("Detail"),
        "creative": st.column_config.TextColumn("Creative"),
        "creative_format": st.column_config.TextColumn("Format"),
        "spend": st.column_config.NumberColumn("Spend", format="$%,.0f"),
        "impressions": st.column_config.NumberColumn("Impr.", format="%,.0f"),
        "clicks": st.column_config.NumberColumn("Clicks", format="%,.0f"),
        "conversions": st.column_config.NumberColumn("Claimed conv.", format="%,.0f"),
        "cost_per_claimed": st.column_config.NumberColumn("Cost/claimed",
                                                          format="$%,.2f"),
    }
    for ch in [c for c in per_spend.index if c in set(ag["channel"])]:
        sub = ag[ag["channel"] == ch].drop(columns=["channel"])
        with st.expander(f"{theme.channel_label(ch)} — {sub['campaign'].nunique()} "
                         f"campaigns, {len(sub)} ad groups"):
            st.dataframe(sub, use_container_width=True, hide_index=True,
                         column_config=ag_cols)
    st.download_button("Download full breakdown (CSV)",
                       ag.to_csv(index=False).encode("utf-8"),
                       "channel_campaign_audience_creative.csv", "text/csv")
