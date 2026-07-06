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
from advanced_reporting.dashboard import insights, theme  # noqa: E402
from advanced_reporting.utils import load_config  # noqa: E402

st.set_page_config(page_title="Advanced Reporting — Overview", layout="wide")
theme.inject_css()

metrics_f = ROOT / "data" / "processed" / "channel_weekly_metrics.csv"
if not metrics_f.exists():
    st.title("Advanced Reporting")
    st.warning("No processed data yet. Run `python scripts/run_pipeline.py` first.")
    st.stop()


@st.cache_data
def _load_weekly(path: str, mtime: float) -> pd.DataFrame:
    return pd.read_csv(path, parse_dates=["date"])


weekly = _load_weekly(str(metrics_f), metrics_f.stat().st_mtime)
cfg = load_config()
rep = cfg.get("reporting", {}) or {}
kpi_label = rep.get("kpi_label", "key events")
budget_cfg = rep.get("budget")

# --- masthead -------------------------------------------------------------------
st.title("How the campaign is doing")
lo, hi = weekly["date"].min(), weekly["date"].max()
n_paid = len(insights._paid_channels(weekly))
st.caption(f"{lo:%d %b %Y} – {hi:%d %b %Y} · {n_paid} paid channels · every number "
           "below is computed from the weekly tables — no generated commentary.")


def _narrow():
    """Editorial measure: keep prose and charts on a readable column width."""
    left, _ = st.columns([7, 2])
    return left


# --- block 1: headline KPI + trend ------------------------------------------------
b = insights.kpi_trend_insight(weekly, kpi_label)
if b:
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

# --- block 2: claims vs measured (the signature honesty visual) -------------------
b = insights.claims_vs_measured_insight(weekly, kpi_label)
if b:
    with _narrow():
        theme.action_title(b["title"])
        per = b["per_channel"]
        labels = [theme.channel_label(c) for c in per["channel"]]
        fig = go.Figure()
        fig.add_bar(x=labels, y=per["claimed"], name="Platform-claimed",
                    marker_color=theme.CLAIMED,
                    text=[f"{r:.1f}x" for r in per["ratio"]], textposition="outside")
        fig.add_bar(x=labels, y=per["measured"], name="Analytics-measured",
                    marker_color=theme.MEASURED)
        fig.update_layout(barmode="group")
        theme.plotly_chart(fig, yfmt="count", height=360)
        theme.prose(b["narrative"])
    st.divider()

# --- block 3: cost per outcome by channel -----------------------------------------
b = insights.cost_per_outcome_insight(weekly, kpi_label)
if b:
    with _narrow():
        theme.action_title(b["title"])
        per = b["per_channel"].sort_values("cost_per", ascending=True)
        fig = go.Figure()
        fig.add_bar(
            y=[theme.channel_label(c) for c in per["channel"]], x=per["cost_per"],
            orientation="h",
            marker_color=[theme.channel_color(c, i)
                          for i, c in enumerate(per["channel"])],
            text=[insights._money(v) for v in per["cost_per"]], textposition="outside")
        fig.update_layout(showlegend=False)
        theme.plotly_chart(fig, xfmt="currency", height=60 + 52 * len(per),
                           legend=False)
        cap = ("per analytics-measured outcome" if b["measured"]
               else "per platform-claimed conversion")
        st.caption(f"Cost {cap}.")
        theme.prose(b["narrative"])
    st.divider()

# --- block 4: pacing ---------------------------------------------------------------
b = insights.pacing_insight(weekly, budget_cfg)
if b:
    with _narrow():
        theme.action_title(b["title"])
        cum = b["cumulative"]
        fig = go.Figure()
        fig.add_scatter(x=cum.index, y=cum.values, name="Cumulative spend",
                        mode="lines", line=dict(color=theme.ACCENT, width=2.5),
                        fill="tozeroy", fillcolor="rgba(31,78,121,0.08)")
        if b["budget"]:
            plan_x = [cum.index.min(),
                      cum.index.min() + pd.Timedelta(weeks=b["budget"]["flight_weeks"])]
            fig.add_scatter(x=plan_x, y=[0, b["budget"]["total"]], name="Plan",
                            mode="lines",
                            line=dict(color=theme.INK_SOFT, width=1.5, dash="dot"))
        theme.plotly_chart(fig, yfmt="currency", height=320,
                           legend=bool(b["budget"]))
        theme.prose(b["narrative"])

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
