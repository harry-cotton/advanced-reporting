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
is_count = mmm_view.is_count_target(meta)
outfmt = "count" if is_count else "currency"
kpi_label = meta.get("kpi_label") or target.replace("_", " ")
outcome_word = kpi_label if is_count else target
st.caption(f"Modeling weekly **{outcome_word}** · every figure is a modeled, uncertainty-"
           "bound **estimate** — validate the big moves with holdout tests before "
           "reallocating budget.")

# --- in plain terms (jargon-free read leads; the charts below carry the detail) --------
plain = mmm_view.plain_summary(summary, meta, run["contributions"])
if plain:
    with st.container(border=True):
        st.markdown("#### In plain terms")
        for para in plain:
            st.markdown(para)
st.divider()

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
    theme.action_title(f"The model tracks weekly {outcome_word} closely — but not perfectly",
                       "Held-out accuracy above is the honest read of this fit.")
    fig = go.Figure()
    fig.add_scatter(x=dates, y=meta.get("actual", []), name="Actual", mode="lines",
                    line=dict(color=theme.INK, width=2))
    fig.add_scatter(x=dates, y=meta.get("predicted", []), name="Model", mode="lines",
                    line=dict(color=theme.ACCENT, width=2, dash="dot"))
    theme.plotly_chart(fig, yfmt=outfmt, height=320)
    st.divider()

# --- contribution waterfall --------------------------------------------------------------
items = mmm_view.waterfall_items(summary, run["contributions"])
if items:
    top_ch = summary.sort_values("contribution", ascending=False).iloc[0]
    theme.action_title(
        f"{theme.channel_label(str(top_ch['channel']))} is the largest estimated "
        f"paid contributor to {outcome_word}",
        "Point estimates; the interval chart below carries the uncertainty.")
    labels = [theme.channel_label(n) if n != "Baseline" else n for n, _v in items]
    _txt = (lambda v: f"{v:,.0f}") if is_count else insights._money
    fig = go.Figure(go.Waterfall(
        x=labels, y=[v for _n, v in items],
        measure=["absolute"] + ["relative"] * (len(items) - 1),
        text=[_txt(v) for _n, v in items], textposition="outside",
        connector=dict(line=dict(color=theme.GRID, width=1)),
        increasing=dict(marker=dict(color=theme.ACCENT)),
        decreasing=dict(marker=dict(color=theme.NEGATIVE)),
        totals=dict(marker=dict(color=theme.INK_SOFT)),
    ))
    theme.plotly_chart(fig, yfmt=outfmt, height=380, legend=False)
    st.caption(f"Baseline = what the model attributes to non-media drivers (seasonality, "
               f"organic demand, controls) — {items[0][1]:,.0f} {outcome_word}. Paid "
               "channels add on top." if is_count else
               "Baseline = what the model attributes to non-media drivers (seasonality, "
               "organic demand, controls). Paid channels add on top.")
    st.divider()

# --- verdicts: cost-per-incremental-outcome (count target) OR ROI-vs-1.0 (currency) ------
if is_count:
    cpo = mmm_view.cost_per_outcome_intervals(summary, meta)
    _v_colors = {"strong": theme.POSITIVE, "cut_candidate": theme.NEGATIVE,
                 "unproven": theme.INK_SOFT}
    good, warn = float(cpo["good"].iloc[0]), float(cpo["warn"].iloc[0])
    n_strong = int((cpo["verdict"] == "strong").sum())
    theme.action_title(
        f"{n_strong} of {len(cpo)} channels beat the ${good:,.0f} cost-per-{kpi_label} "
        "target with statistical confidence",
        f"Dot = modeled cost per incremental {kpi_label}, bar = 90% interval. Graded "
        f"against the client band (good ≤ ${good:,.0f}, watch ≤ ${warn:,.0f}); an "
        "interval reaching off the chart = the model can't rule out zero effect (unproven).")
    x_cap = warn * 2.5                          # clip 'infinite' worst-cases so the axis reads
    fig = go.Figure()
    fig.add_vrect(x0=0, x1=good, fillcolor=theme.POSITIVE, opacity=0.06, line_width=0)
    fig.add_vline(x=good, line=dict(color=theme.INK_SOFT, width=1, dash="dash"))
    fig.add_vline(x=warn, line=dict(color=theme.INK_SOFT, width=1, dash="dot"))
    for _, r in cpo.iloc[::-1].iterrows():
        color = _v_colors[r["verdict"]]
        label = theme.channel_label(str(r["channel"]))
        lo = min(r["cost_low"], x_cap)
        hi = min(r["cost_high"], x_cap) if pd.notna(r["cost_high"]) else x_cap
        fig.add_scatter(x=[lo, hi], y=[label, label], mode="lines",
                        line=dict(color=color, width=5), opacity=0.35, showlegend=False,
                        hoverinfo="skip")
        if pd.notna(r["cost_per"]) and r["cost_per"] <= x_cap:
            fig.add_scatter(x=[r["cost_per"]], y=[label], mode="markers", showlegend=False,
                            name=label, marker=dict(color=color, size=12))
    fig.update_xaxes(range=[0, x_cap])
    theme.plotly_chart(fig, xfmt="currency", height=110 + 44 * len(cpo), legend=False)
    st.caption(f"“Media buys applications; it cannot pass a polygraph.” Cost per "
               f"incremental **submitted application** (the MMM target) — the post-"
               "submission pipeline is downstream and not modeled. Provenance: client target.")
else:
    roi = mmm_view.roi_intervals(summary)
    _v_colors = {"profitable": theme.POSITIVE, "unprofitable": theme.NEGATIVE,
                 "unproven": theme.INK_SOFT}
    n_solid = int((roi["verdict"] == "profitable").sum())
    theme.action_title(
        f"{n_solid} of {len(roi)} channels are profitable with statistical confidence",
        "Dot = point ROI, bar = 90% interval. Only intervals clear of the 1.0 line are "
        "conclusive; everything else is unproven, not bad.")
    fig = go.Figure()
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
    fig.update_yaxes(title_text=f"Modeled weekly {outcome_word}", title_font=dict(size=12))
    theme.plotly_chart(fig, yfmt=outfmt, xfmt="currency", height=420)

st.divider()
st.caption("Estimates are correlational, not proven causation; the 90% intervals are "
           "the honest read. The planner (`scripts/plan_campaign.py`) optimizes budgets "
           "against these curves under the committed rails.")
