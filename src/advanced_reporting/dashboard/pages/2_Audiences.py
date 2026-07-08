"""Audiences — the optimization view (redesign R4).

Audience cost ranking, spend-share vs performance-share, creative-format comparison
and trends for the top audiences — all decoded from the naming convention at ingest.

HONESTY: GA4 key events are campaign-level. EVERY conversion number on this page is
platform-claimed; columns and captions say so, and nothing here implies GA4
verification below campaign grain.
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
from advanced_reporting.ingestion.naming_decode import UNPARSED  # noqa: E402

st.set_page_config(page_title="Audiences — Advanced Reporting", layout="wide")
theme.inject_css()
st.title("Audiences")
st.caption("Decoded from ad-set/ad names via the naming convention. **All conversion "
           "numbers on this page are platform-claimed** — GA4 measures outcomes at "
           "campaign grain, not per audience or creative.")

history_f = ROOT / "data" / "processed" / "history.parquet"
if not history_f.exists():
    st.warning("No store yet. Run `python scripts/ingest.py --inbox` first.")
    st.stop()


@st.cache_data
def _load(path: str, mtime: float) -> pd.DataFrame:
    return pd.read_parquet(path)


hist = _load(str(history_f), history_f.stat().st_mtime)
aud = drilldown.audience_summary(hist)
if aud.empty:
    st.info("No audience-decoded rows in the store yet — drop ad-set/ad-group exports "
            "in `data/inbox/` and re-ingest.")
    st.stop()

unp = drilldown.unparsed_stats(hist)
known = aud[aud["audience_type"] != UNPARSED]

# --- metric tile row ----------------------------------------------------------------
_tcols = st.columns(3)
with _tcols[0]:
    theme.metric_card("Ad-level spend", insights._money(float(aud["spend"].sum())))
with _tcols[1]:
    theme.metric_card("Audiences decoded", str(len(known)))
with _tcols[2]:
    theme.metric_card("Unparsed spend", f"{unp['spend_rate'] * 100:.0f}%",
                      help="Share of ad-level spend under names the convention can't decode.")
st.divider()

# --- unparsed-rate callout (the adoption pitch) --------------------------------------
if unp["names"]:
    st.info(f"**{unp['spend_rate'] * 100:.0f}% of ad-level spend "
            f"(\\${unp['spend']:,.0f}) runs under names the convention can't "
            f"decode** ({unp['row_rate'] * 100:.0f}% of rows). It reports as "
            "\"(unparsed)\" below rather than being guessed. Renaming these fixes it: "
            f"{', '.join(f'`{n}`' for n in unp['names'])}.")
else:
    st.caption("Every ad-level name decoded cleanly — 0% unparsed.")

# --- cost ranking + spend share side by side ----------------------------------------
_left, _right = st.columns([1, 1])
with _left:
    if len(known) >= 2:
        best, worst = known.iloc[0], known.iloc[-1]
        mult = worst["cost_per_claimed"] / best["cost_per_claimed"]
        theme.action_title(
            f"{best['audience_type']} · {best['audience_detail']} converts at "
            f"{mult:.1f}x less cost than {worst['audience_type']} · "
            f"{worst['audience_detail']}",
            "Cost per platform-claimed conversion, cheapest first.")
    else:
        theme.action_title("Cost per platform-claimed conversion by audience")
    labels = [f"{t} · {d}" if t != UNPARSED else UNPARSED
              for t, d in zip(aud["audience_type"], aud["audience_detail"])]
    colors = [theme.INK_SOFT if t == UNPARSED
              else theme.ACCENT if t == "PROSPECT" else theme.CLAIMED
              for t in aud["audience_type"]]
    fig = go.Figure(go.Bar(
        y=labels[::-1], x=aud["cost_per_claimed"][::-1], orientation="h",
        marker_color=colors[::-1],
        text=[f"${v:,.0f}" for v in aud["cost_per_claimed"]][::-1],
        textposition="outside"))
    theme.plotly_chart(fig, xfmt="currency",
                       height=max(400, 60 + 44 * len(aud)), legend=False)
    st.caption("Blue = prospecting, amber = retargeting (warm audiences convert cheaper "
               "by construction — compare within a type, not across), gray = unparsed.")

with _right:
    gap = (known["claimed_share"] - known["spend_share"])
    if len(known) >= 2:
        star = known.loc[gap.idxmax()]
        theme.action_title(
            f"{star['audience_type']} · {star['audience_detail']} wins "
            f"{star['claimed_share'] * 100:.0f}% of claimed conversions on "
            f"{star['spend_share'] * 100:.0f}% of spend",
            "Above the line = earning more than its share of budget.")
        fig = go.Figure()
        lim = max(known["spend_share"].max(), known["claimed_share"].max()) * 1.2
        fig.add_scatter(x=[0, lim], y=[0, lim], mode="lines", showlegend=False,
                        line=dict(color=theme.GRID, width=1, dash="dot"))
        for i, r in known.iterrows():
            fig.add_scatter(
                x=[r["spend_share"]], y=[r["claimed_share"]], mode="markers+text",
                text=[f"{r['audience_type']} · {r['audience_detail']}"],
                textposition="top center", textfont=dict(size=11, color=theme.INK_SOFT),
                marker=dict(size=12, color=theme.ACCENT if r["audience_type"] == "PROSPECT"
                            else theme.CLAIMED),
                showlegend=False)
        fig.update_xaxes(title_text="Share of spend", title_font=dict(size=12))
        fig.update_yaxes(title_text="Share of claimed conversions",
                         title_font=dict(size=12))
        theme.plotly_chart(fig, xfmt="pct", yfmt="pct", height=400, legend=False)

# --- creative formats ------------------------------------------------------------------
cre = drilldown.creative_summary(hist)
if not cre.empty:
    if len(cre) >= 2:
        b = cre.iloc[0]
        theme.action_title(
            f"{b['creative']} ({b['creative_format']}) is the most efficient creative "
            f"at ${b['cost_per_claimed']:,.0f} per claimed conversion",
            "Creative names decoded from the Ad grammar (e.g. LinkedIn creatives).")
    else:
        theme.action_title("Creative performance (platform-claimed)")
    fig = go.Figure(go.Bar(
        y=[f"{c} · {f}" for c, f in zip(cre["creative"], cre["creative_format"])][::-1],
        x=cre["cost_per_claimed"][::-1], orientation="h",
        marker_color=[theme.MEASURED if f == "VID" else theme.ACCENT
                      for f in cre["creative_format"]][::-1],
        text=[f"${v:,.0f}" for v in cre["cost_per_claimed"]][::-1],
        textposition="outside"))
    theme.plotly_chart(fig, xfmt="currency", height=80 + 40 * len(cre), legend=False)

# --- trend for the top audiences --------------------------------------------------------
tr = drilldown.audience_weekly(hist)
if not tr.empty:
    tots = tr.groupby("audience")["conversions"].sum().sort_values(ascending=False)
    theme.action_title(
        f"{tots.index[0]} drives the most claimed conversions week after week",
        "Weekly platform-claimed conversions, top audiences by spend.")
    fig = go.Figure()
    for i, a in enumerate(tots.index):
        g = tr[tr["audience"] == a]
        fig.add_scatter(x=g["date"], y=g["conversions"], name=a, mode="lines",
                        line=dict(width=2))
    theme.plotly_chart(fig, yfmt="count", height=340)

# --- the full table ---------------------------------------------------------------------
st.divider()
st.dataframe(
    aud, use_container_width=True, hide_index=True,
    column_config={
        "audience_type": st.column_config.TextColumn("Audience type"),
        "audience_detail": st.column_config.TextColumn("Detail"),
        "channel": st.column_config.TextColumn("Channels"),
        "spend": st.column_config.NumberColumn("Spend", format="$%,.0f"),
        "impressions": st.column_config.NumberColumn("Impr.", format="%,.0f"),
        "clicks": st.column_config.NumberColumn("Clicks", format="%,.0f"),
        "conversions": st.column_config.NumberColumn("Claimed conv.", format="%,.0f"),
        "cost_per_claimed": st.column_config.NumberColumn("Cost/claimed",
                                                          format="$%,.2f"),
        "spend_share": st.column_config.NumberColumn("Spend share", format="percent"),
        "claimed_share": st.column_config.NumberColumn("Claimed share",
                                                       format="percent"),
    })
st.download_button("Download audience table (CSV)",
                   aud.to_csv(index=False).encode("utf-8"),
                   "audience_performance.csv", "text/csv")
