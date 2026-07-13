"""Results — the MMM page (redesign U3). Appears populated only when a model run's
artifacts exist in outputs/ (a descriptive run deletes them, so stale models from a
different dataset can never render as current).

Engine-agnostic: renders the persisted MMMResult shape, so the baseline engine and
Meridian (later) share this page. Every figure is a modeled ESTIMATE — intervals and
hedged language are part of the design, not decoration.
"""
from __future__ import annotations
import sys
from pathlib import Path
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
from advanced_reporting.dashboard import insights, mmm_view, theme  # noqa: E402

st.set_page_config(page_title="Advanced Reporting — Incrementality", layout="wide")
theme.inject_css()
theme.nav_bar()
st.title("Incrementality")

run = mmm_view.load_mmm(ROOT / "outputs")
if run is None:
    st.subheader("Not unlocked yet — this is the causal layer above the descriptive report")
    st.markdown(
        "The rest of this product answers **what happened** (spend, delivery, "
        "analytics-measured outcomes). Incrementality answers **what the media actually "
        "caused** — the question a budget decision really turns on:\n\n"
        "- **Which channels truly drive outcomes** vs. ride on demand that would convert "
        "anyway (the claims-vs-measured gap hints at this; a model proves it).\n"
        "- **Where the next dollar works hardest** — response curves and diminishing "
        "returns per channel.\n"
        "- **How to reallocate budget** for more outcomes at the same spend, with 90% "
        "confidence intervals so you know what's solid vs. unproven.")
    st.info("**What it needs:** a weekly business-KPI series matched back to media — e.g. "
            "a CRM matchback of applications (then enrollments/hires). That's exactly the "
            "**Unlock incrementality measurement** recommendation on the Exec Summary. "
            "Once the series exists and `python scripts/run_pipeline.py` fits the model, "
            "the contribution waterfall, ROI intervals and response curves render here.")
    st.caption("The model stays engine-agnostic (baseline now, Google Meridian later) and "
               "every figure lands with a 90% interval — modeled estimates, not proof.")
    st.stop()

summary, meta = run["summary"], run["meta"]
target = meta.get("target", "revenue")
st.caption(f"Modeling weekly **{target}** · every figure is a modeled, uncertainty-"
           "bound **estimate** — validate the big moves with holdout tests before "
           "reallocating budget.")

# --- fit strip ------------------------------------------------------------------------
cards = mmm_view.fit_cards(meta)
for col, (label, value, help_) in zip(st.columns(len(cards)), cards):
    with col:
        theme.metric_card(label, value, help=help_)
fm = meta.get("fit_metrics") or {}
if (fm.get("r2") or 0) - (fm.get("test_r2") or 0) > 0.15:
    st.warning("The in-sample/held-out gap suggests some overfitting — treat "
               "channel-level estimates with extra caution.")
st.divider()

# --- actual vs predicted ---------------------------------------------------------------
dates = pd.to_datetime(pd.Series(meta.get("dates", [])))
if len(dates):
    theme.action_title(f"The model tracks weekly {target} closely — but not perfectly",
                       "Held-out accuracy above is the honest read of this fit.")
    fig = go.Figure()
    fig.add_scatter(x=dates, y=meta.get("actual", []), name="Actual", mode="lines",
                    line=dict(color=theme.INK, width=2))
    fig.add_scatter(x=dates, y=meta.get("predicted", []), name="Model", mode="lines",
                    line=dict(color=theme.ACCENT, width=2, dash="dot"))
    theme.plotly_chart(fig, yfmt="currency", height=320)
    st.divider()

# --- contribution waterfall --------------------------------------------------------------
items = mmm_view.waterfall_items(summary, run["contributions"])
if items:
    top_ch = summary.sort_values("contribution", ascending=False).iloc[0]
    theme.action_title(
        f"{theme.channel_label(str(top_ch['channel']))} is the largest estimated "
        f"paid contributor to {target}",
        "Point estimates; the interval chart below carries the uncertainty.")
    labels = [theme.channel_label(n) if n != "Baseline" else n for n, _v in items]
    fig = go.Figure(go.Waterfall(
        x=labels, y=[v for _n, v in items],
        measure=["absolute"] + ["relative"] * (len(items) - 1),
        text=[insights._money(v) for _n, v in items], textposition="outside",
        connector=dict(line=dict(color=theme.GRID, width=1)),
        increasing=dict(marker=dict(color=theme.ACCENT)),
        decreasing=dict(marker=dict(color=theme.NEGATIVE)),
        totals=dict(marker=dict(color=theme.INK_SOFT)),
    ))
    theme.plotly_chart(fig, yfmt="currency", height=380, legend=False)
    st.caption("Baseline = what the model attributes to non-media drivers (seasonality, "
               "organic demand, controls). Paid channels add on top.")
    st.divider()

# --- ROI intervals -------------------------------------------------------------------------
roi = mmm_view.roi_intervals(summary)
_v_colors = {"profitable": theme.POSITIVE, "unprofitable": theme.NEGATIVE,
             "unproven": theme.INK_SOFT}
n_solid = int((roi["verdict"] == "profitable").sum())
theme.action_title(
    f"{n_solid} of {len(roi)} channels are profitable with statistical confidence",
    "Dot = point ROI, bar = 90% interval. Only intervals clear of the 1.0 line are "
    "conclusive; everything else is unproven, not bad.")
fig = go.Figure()
# break-even reference line: neutral (amber now strictly means "platform-claimed")
fig.add_vline(x=1.0, line=dict(color=theme.INK_SOFT, width=1.5, dash="dash"))
for _, r in roi.iloc[::-1].iterrows():
    color = _v_colors[r["verdict"]]
    label = theme.channel_label(str(r["channel"]))
    fig.add_scatter(x=[r["roi_low"], r["roi_high"]], y=[label, label], mode="lines",
                    line=dict(color=color, width=5), opacity=0.35, showlegend=False,
                    hoverinfo="skip")
    fig.add_scatter(x=[r["roi"]], y=[label], mode="markers", showlegend=False,
                    name=label, marker=dict(color=color, size=12))
theme.plotly_chart(fig, xfmt="ratio", height=110 + 44 * len(roi), legend=False)
st.divider()

# --- response curves --------------------------------------------------------------------------
curves = mmm_view.response_curves(meta)
if curves:
    theme.action_title(
        "Where the next dollar works hardest — and where returns are flattening",
        "Modeled weekly response vs spend; the dot marks each channel's average "
        "weekly spend. Curves bending flat = diminishing returns.")
    fig = go.Figure()
    for i, (ch, c) in enumerate(sorted(curves.items())):
        color = theme.channel_color(ch, i)
        fig.add_scatter(x=c["spend"], y=c["response"], mode="lines",
                        name=theme.channel_label(ch), line=dict(color=color, width=2))
        spend_grid, resp = list(c["spend"]), list(c["response"])
        ms = float(c.get("mean_spend", 0.0))
        if spend_grid and ms:
            j = min(range(len(spend_grid)), key=lambda k: abs(spend_grid[k] - ms))
            fig.add_scatter(x=[spend_grid[j]], y=[resp[j]], mode="markers",
                            showlegend=False, hoverinfo="skip",
                            marker=dict(color=color, size=10,
                                        line=dict(color=theme.PAPER, width=1.5)))
    fig.update_xaxes(title_text="Weekly spend", title_font=dict(size=12))
    fig.update_yaxes(title_text=f"Modeled weekly {target}", title_font=dict(size=12))
    theme.plotly_chart(fig, yfmt="currency", xfmt="currency", height=420)

st.divider()
st.caption("Estimates are correlational, not proven causation; the 90% intervals are "
           "the honest read. The planner (`scripts/plan_campaign.py`) optimizes budgets "
           "against these curves under the committed rails.")
