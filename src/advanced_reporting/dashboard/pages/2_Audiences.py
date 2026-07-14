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
from advanced_reporting.dashboard import drilldown, filters, insights, theme  # noqa: E402
from advanced_reporting.ingestion.naming_decode import UNPARSED  # noqa: E402

st.set_page_config(page_title="Advanced Reporting — Audiences", layout="wide")
theme.inject_css()
theme.nav_bar()
st.title("Audiences")
st.caption("Decoded from ad-set/ad names via the naming convention. **All conversion "
           "numbers on this page are platform-claimed** — analytics measures outcomes at "
           "campaign grain, not per audience or creative.")

# audience-TYPE colours: never the reserved amber/ink (every figure here is claimed, so
# amber-for-retargeting would clash with amber-for-claimed) and never RAG red/green
_ATYPE = {"PROSPECT": "#4E79A7", "RETARGET": "#2A9D8F"}


def _atype_color(t: str) -> str:
    from advanced_reporting.ingestion.naming_decode import UNPARSED as _U
    return theme.GHOST if t == _U else _ATYPE.get(t, theme.INK_SOFT)

history_f = ROOT / "data" / "processed" / "history.parquet"
if not history_f.exists():
    st.warning("No store yet. Run `python scripts/ingest.py --inbox` first.")
    st.stop()


@st.cache_data
def _load(path: str, mtime: float) -> pd.DataFrame:
    return pd.read_parquet(path)


hist = _load(str(history_f), history_f.stat().st_mtime)
_dates = pd.to_datetime(hist["date"])
_dr, _chsel = filters.sidebar_filters(
    hist["channel"].dropna().unique(), _dates.min().date(), _dates.max().date())
hist = filters.apply(hist, _dr, _chsel)
if hist.empty:
    st.info("No rows for the current filter — widen the date range or channel selection.")
    st.stop()
aud = drilldown.audience_summary(hist)
if aud.empty:
    st.info("No audience-decoded rows in the store yet — drop ad-set/ad-group exports "
            "in `data/inbox/` and re-ingest.")
    st.stop()

# The ad-level (decoded) rows can cover a different window than the Overview's flight —
# say so, so a reader never reconciles this page's totals against the headline period.
_ad = hist[hist["spend"].notna() & (hist["ad_group"].fillna("") != "")]
if not _ad.empty:
    _alo, _ahi = pd.to_datetime(_ad["date"]).min(), pd.to_datetime(_ad["date"]).max()
    st.caption(f"⚠ Ad-level data covers **{_alo:%d %b %Y} – {_ahi:%d %b %Y}** — a "
               "different window than the Exec Summary flight; totals here won't tie out "
               "to the headline period.")

unp = drilldown.unparsed_stats(hist)
known = aud[aud["audience_type"] != UNPARSED]

# --- dense totals card ("Media Totals" flavor) --------------------------------------
theme.metric_grid("Ad-level totals", [
    ("Ad-level spend", insights._money(float(aud["spend"].sum()))),
    ("Audiences decoded", str(len(known))),
    ("Unparsed spend", f"{unp['spend_rate'] * 100:.0f}%"),
], cols=3)
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
    # The headline comparison stays WITHIN one audience type — a cross-type "LAL beats
    # SITE-90D" claim is exactly the warm-vs-cold misread the caption below warns about.
    # Take the type with the widest within-type spread (same rule as the Exec block).
    _grps = [g for _, g in known.groupby("audience_type", sort=False) if len(g) >= 2]
    if _grps:
        _g = max(_grps, key=lambda g: (g["cost_per_claimed"].iloc[-1]
                                       / g["cost_per_claimed"].iloc[0]))
        best, worst = _g.iloc[0], _g.iloc[-1]
        mult = worst["cost_per_claimed"] / best["cost_per_claimed"]
        theme.action_title(
            f"Among {best['audience_type']} audiences, {best['audience_detail']} is "
            f"{mult:.1f}× cheaper per claimed conversion than {worst['audience_detail']}",
            "Cost per platform-claimed conversion, cheapest first.")
    else:
        theme.action_title("Cost per platform-claimed conversion by audience")
    labels = [f"{t} · {d}" if t != UNPARSED else UNPARSED
              for t, d in zip(aud["audience_type"], aud["audience_detail"])]
    colors = [_atype_color(t) for t in aud["audience_type"]]
    fig = go.Figure(go.Bar(
        y=labels[::-1], x=aud["cost_per_claimed"][::-1], orientation="h",
        marker_color=colors[::-1],
        text=[f"${v:,.0f}" for v in aud["cost_per_claimed"]][::-1],
        textposition="outside"))
    fig.update_xaxes(range=[0, float(aud["cost_per_claimed"].max()) * 1.18])
    theme.plotly_chart(fig, xfmt="currency",
                       height=max(400, 60 + 44 * len(aud)), legend=False)
    st.caption("Slate = prospecting, teal = retargeting (warm audiences convert cheaper "
               "by construction — compare within a type, not across), gray = unparsed.")

with _right:
    gap = (known["claimed_share"] - known["spend_share"])
    if len(known) >= 2:
        star = known.loc[gap.idxmax()]
        theme.action_title(
            f"{star['audience_type']} · {star['audience_detail']} wins "
            f"{star['claimed_share'] * 100:.0f}% of claimed conversions on "
            f"{star['spend_share'] * 100:.0f}% of spend",
            "Budget share → claimed-conversion share. Green = earning more than its "
            "share of budget; red = less.")
        ranked = known.sort_values("claimed_share", ascending=False)
        fig = theme.dumbbell_fig(
            [f"{t} · {d}" for t, d in zip(ranked["audience_type"],
                                          ranked["audience_detail"])],
            ranked["spend_share"], ranked["claimed_share"],
            from_name="Share of spend", to_name="Share of claimed conv.",
            fmt="pct", height=400)
        theme.plotly_chart(fig, height=400)

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
        # distinct hues per format (the old MEASURED/ACCENT were the same ink) — and not
        # the reserved amber/ink, since these are platform-claimed figures
        marker_color=[("#2A9D8F" if f == "VID" else "#9C6BA3")
                      for f in cre["creative_format"]][::-1],
        text=[f"${v:,.0f}" for v in cre["cost_per_claimed"]][::-1],
        textposition="outside"))
    fig.update_xaxes(range=[0, float(cre["cost_per_claimed"].max()) * 1.18])
    theme.plotly_chart(fig, xfmt="currency", height=80 + 40 * len(cre), legend=False)
    st.caption("Teal = video, plum = static/other. All figures platform-claimed.")

    # --- what messaging works WHERE: creative x career path ---------------------------
    ci = drilldown.creative_initiative_table(hist)
    ci = ci[ci["conversions"].fillna(0) > 0]
    if not ci.empty and ci["initiative"].nunique() >= 2:
        best = ci.loc[ci["cost_per_claimed"].idxmin()]
        _spread = (float(ci["cost_per_claimed"].max())
                   / float(ci["cost_per_claimed"].min()))
        # only claim a winner when the spread is material — "works hardest" on a 4%
        # gap is exactly the over-claim the honesty voice exists to prevent
        if _spread >= 1.15:
            _t = (f"{best['creative']} works hardest for {best['initiative']} — "
                  f"${float(best['cost_per_claimed']):,.0f} per claimed conversion")
        else:
            _t = ("Creative costs are flat across career paths — no message is "
                  "out-earning the others yet")
        theme.action_title(
            _t, "Cost per platform-claimed conversion, creative × career path (the "
            "initiative decoded from the campaign name).")
        pv = (ci.pivot_table(index=["creative", "creative_format"],
                             columns="initiative", values="cost_per_claimed",
                             aggfunc="first"))
        pv.index = [f"{c} · {f}" for c, f in pv.index]
        pv = pv.reset_index().rename(columns={"index": "creative"})
        st.dataframe(
            pv, use_container_width=True, hide_index=True,
            column_config={c: (st.column_config.TextColumn("Creative") if c == "creative"
                               else st.column_config.NumberColumn(c, format="$%,.0f"))
                           for c in pv.columns})
        st.caption(
            "Cost per **platform-claimed** conversion per cell — read across a row to "
            "see where a message earns its keep. _Creative names decode only where ad "
            "names follow the Ad grammar (LinkedIn here); adopting the naming "
            "convention on Meta/Google ads would unlock this view across every "
            "channel._")

# --- trend for the top audiences --------------------------------------------------------
tr = drilldown.audience_weekly(hist)
if not tr.empty:
    tots = tr.groupby("audience")["conversions"].sum().sort_values(ascending=False)
    theme.action_title(
        f"{tots.index[0]} drives the most claimed conversions month after month",
        "Monthly platform-claimed conversions, top audiences by spend (weekly lines "
        "over a 131-week flight read as noise).")
    # stable muted hues (never the amber/ink honesty pair or RAG saturations) — the
    # plotly default rainbow is off-palette for the house style
    _line_colors = ["#4E79A7", "#B0623A", "#2A9D8F", "#9C6BA3", "#6B8E23", "#B8860B"]
    _mo = tr.copy()
    _mo["month"] = pd.to_datetime(_mo["date"]).dt.to_period("M").dt.to_timestamp()
    _mo = _mo.groupby(["month", "audience"], as_index=False)["conversions"].sum()
    fig = go.Figure()
    for i, a in enumerate(tots.index):
        g = _mo[_mo["audience"] == a]
        fig.add_scatter(x=g["month"], y=g["conversions"], name=a, mode="lines",
                        line=dict(width=2, color=_line_colors[i % len(_line_colors)]))
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
