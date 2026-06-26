"""Streamlit dashboard for standard (non-MMM) campaign metrics.

Run:  streamlit run src/advanced_reporting/dashboard/app.py
Reads processed files produced by scripts/run_pipeline.py.
"""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

st.set_page_config(page_title="Advanced Reporting", layout="wide")
st.title("Advanced Reporting — Campaign Dashboard")

metrics_f = ROOT / "data" / "processed" / "channel_weekly_metrics.csv"
summary_f = ROOT / "outputs" / "channel_summary.csv"

if not metrics_f.exists():
    st.warning("No processed data yet. Run `python scripts/run_pipeline.py` first.")
    st.stop()

m = pd.read_csv(metrics_f, parse_dates=["date"])
channels = sorted(m["channel"].unique())
sel = st.sidebar.multiselect("Channels", channels, default=channels)
dr = st.sidebar.date_input("Date range", (m["date"].min(), m["date"].max()))
f = m[m["channel"].isin(sel)]
if isinstance(dr, tuple) and len(dr) == 2:
    f = f[(f["date"] >= pd.Timestamp(dr[0])) & (f["date"] <= pd.Timestamp(dr[1]))]

spend, rev, conv = f["spend"].sum(), f["platform_revenue"].sum(), f["conversions"].sum()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Spend", f"${spend/1e6:.2f}M")
c2.metric("Platform revenue", f"${rev/1e6:.2f}M")
c3.metric("Blended ROAS", f"{(rev/spend if spend else 0):.2f}x")
c4.metric("Avg CPA", f"${(spend/conv if conv else 0):,.0f}")

st.subheader("Spend & revenue over time")
st.line_chart(f.groupby("date")[["spend", "platform_revenue"]].sum())

st.subheader("Channel performance")
agg = f.groupby("channel").agg(
    spend=("spend", "sum"), impressions=("impressions", "sum"), clicks=("clicks", "sum"),
    conversions=("conversions", "sum"), platform_revenue=("platform_revenue", "sum")).reset_index()
agg["CTR %"] = (agg.clicks / agg.impressions * 100).round(2)
agg["CVR %"] = (agg.conversions / agg.clicks * 100).round(2)
agg["CPA"] = (agg.spend / agg.conversions).round(0)
agg["ROAS"] = (agg.platform_revenue / agg.spend).round(2)
st.dataframe(agg, use_container_width=True)

if summary_f.exists():
    st.subheader("MMM — estimated contribution & ROI (90% intervals)")
    st.dataframe(pd.read_csv(summary_f), use_container_width=True)
    st.caption("MMM figures are modeled, uncertainty-bound estimates — validate with "
               "experiments before reallocating budget.")
