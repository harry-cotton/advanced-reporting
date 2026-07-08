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
from advanced_reporting.dashboard import drilldown, insights, theme  # noqa: E402

st.set_page_config(page_title="Channels — Advanced Reporting", layout="wide")
theme.inject_css()
st.title("Channels")
st.caption("Trends, efficiency and the campaign → audience → creative breakdown. "
           "Key events are GA4-measured at campaign grain; everything below that is "
           "**platform-claimed**.")

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

paid = insights._paid_channels(weekly)
sel = st.sidebar.multiselect("Channels", paid, default=paid)
weekly = weekly[weekly["channel"].isin(sel)]
hist = hist[hist["channel"].isin(sel)]
if weekly.empty:
    st.info("No rows for the current filter.")
    st.stop()

# --- metric tile row ----------------------------------------------------------------
_measured = "key_events" in weekly.columns and weekly["key_events"].notna().any()
_out_col = "key_events" if _measured else "conversions"
_out_label = "Key events" if _measured else "Claimed conv."
_tcols = st.columns(4)
with _tcols[0]:
    theme.metric_card("Spend", insights._money(float(weekly["spend"].sum())))
with _tcols[1]:
    theme.metric_card("Impressions", f"{weekly['impressions'].sum():,.0f}")
with _tcols[2]:
    theme.metric_card("Clicks", f"{weekly['clicks'].sum():,.0f}")
with _tcols[3]:
    theme.metric_card(_out_label, f"{float(weekly[_out_col].sum()):,.0f}")
st.divider()

# --- weekly spend trend + mix -------------------------------------------------------
per_spend = weekly.groupby("channel")["spend"].sum().sort_values(ascending=False)
top_ch, top_share = per_spend.index[0], per_spend.iloc[0] / per_spend.sum()
theme.action_title(
    f"{theme.channel_label(top_ch)} takes {top_share * 100:.0f}% of paid spend")
_left, _right = st.columns([2, 1])
with _left:
    ts = weekly.groupby(["date", "channel"], as_index=False)["spend"].sum().sort_values("date")
    fig = go.Figure()
    for i, ch in enumerate(per_spend.index):
        g = ts[ts["channel"] == ch]
        fig.add_scatter(x=g["date"], y=g["spend"], name=theme.channel_label(ch),
                        mode="lines", line=dict(color=theme.channel_color(ch, i), width=2),
                        stackgroup="spend")
    theme.plotly_chart(fig, yfmt="currency", height=340)
with _right:
    mix = insights.spend_mix(weekly)
    fig = go.Figure(go.Pie(
        labels=[theme.channel_label(c) for c in mix["channel"]],
        values=mix["spend"], hole=0.62, sort=False,
        marker=dict(colors=[theme.channel_color(c, i)
                            for i, c in enumerate(mix["channel"])]),
        textinfo="percent", textfont=dict(size=12)))
    fig.update_layout(annotations=[dict(text="Spend<br>mix", showarrow=False,
                      font=dict(family=theme.SANS, size=14, color=theme.INK_SOFT))])
    theme.plotly_chart(fig, height=340, legend=False)

# --- efficiency view ----------------------------------------------------------------
eff = insights.cost_per_outcome_insight(weekly)
if eff:
    theme.action_title(eff["title"])
    per = eff["per_channel"]
    outcome_col = "key_events" if eff["measured"] else "conversions"
    fig = go.Figure()
    for i, r in per.iterrows():
        fig.add_scatter(
            x=[r["spend"]], y=[r["cost_per"]], mode="markers+text",
            text=[theme.channel_label(r["channel"])], textposition="top center",
            textfont=dict(size=12, color=theme.INK_SOFT),
            marker=dict(size=max(10.0, float(r[outcome_col]) ** 0.5),
                        color=theme.channel_color(r["channel"], i), opacity=0.85),
            name=theme.channel_label(r["channel"]))
    fig.update_xaxes(title_text="Spend", title_font=dict(size=12))
    fig.update_yaxes(title_text=f"Cost / {eff['outcome_label']}",
                     title_font=dict(size=12))
    theme.plotly_chart(fig, yfmt="currency", xfmt="currency", height=380, legend=False)
    st.caption("Bubble size = outcome volume. "
               + ("Outcomes are analytics-measured (GA4)." if eff["measured"]
                  else "Outcomes are platform-claimed — no analytics series yet."))

# --- campaign table ------------------------------------------------------------------
theme.action_title("Campaigns, ranked by spend within channel",
                   "Key events + cost/key event are GA4-measured at this grain.")
camp = drilldown.campaign_table(hist)
st.dataframe(
    camp, use_container_width=True, hide_index=True,
    column_config={
        "channel": st.column_config.TextColumn("Channel"),
        "campaign": st.column_config.TextColumn("Campaign", width="large"),
        "spend": st.column_config.NumberColumn("Spend", format="$%,.0f"),
        "impressions": st.column_config.NumberColumn("Impr.", format="%,.0f"),
        "clicks": st.column_config.NumberColumn("Clicks", format="%,.0f"),
        "conversions": st.column_config.NumberColumn("Claimed conv.", format="%,.0f"),
        "key_events": st.column_config.NumberColumn("Key events (GA4)", format="%,.0f"),
        "cost_per_claimed": st.column_config.NumberColumn("Cost/claimed",
                                                          format="$%,.2f"),
        "cost_per_key_event": st.column_config.NumberColumn("Cost/key event",
                                                            format="$%,.2f"),
    })
st.download_button("Download campaign table (CSV)",
                   camp.to_csv(index=False).encode("utf-8"),
                   "campaigns.csv", "text/csv")

# --- nested breakdown ----------------------------------------------------------------
theme.action_title("Inside each channel: campaign → audience → creative",
                   "Ad-set/ad-group grain with the decoded naming fields. Conversions "
                   "at this grain are platform-claimed ONLY.")
ag = drilldown.ad_group_table(hist)
if ag.empty:
    st.caption("No ad-level rows in the store yet — drop ad-set/ad-group exports in "
               "`data/inbox/` and re-ingest.")
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
