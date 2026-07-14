"""Geography — where application starts come from (P5 round-2 feedback).

A national heat map (regions painted onto their member states via the config
``data.geo_states`` mapping) plus the per-region table: GA4-measured application
starts, CRM submitted applications, paid spend, and an over/under-index vs
population share. Engagements without a ``geo_states`` mapping degrade to a bar
ranking — the page never guesses which states a custom region contains.

HONESTY: starts are GA4-measured (all traffic); submitted applications are the CRM
matchback; the population index is descriptive — where demand concentrates, never
which media caused it.
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
from advanced_reporting.utils import load_config, scope_to_sources  # noqa: E402

st.set_page_config(page_title="Advanced Reporting — Geography", layout="wide")
theme.inject_css()
theme.nav_bar()
st.title("Geography")

cfg = load_config()
_data = cfg.get("data") or {}
GEO_LABELS: dict = _data.get("geo_labels") or {}
GEO_STATES: dict = _data.get("geo_states") or {}
POPS: dict = _data.get("geo_populations") or {}
_rep = cfg.get("reporting") or {}
_spec, _ = load_active_spec(ROOT)
KPI = _rep.get("kpi_label") or _spec.get("kpi_label") or "key events"


def _geo_label(code: str) -> str:
    return GEO_LABELS.get(code, str(code))


st.caption(f"Where **{KPI}** come from: GA4-measured (all traffic) by field-office "
           "region, with the CRM's submitted applications alongside. Descriptive — "
           "regional demand, not media attribution.")

history_f = ROOT / "data" / "processed" / "history.parquet"
if not history_f.exists():
    st.warning("No store yet. Run `python scripts/ingest.py --inbox` first.")
    st.stop()


@st.cache_data
def _load_hist(path: str, mtime: float) -> pd.DataFrame:
    return pd.read_parquet(path)


@st.cache_data
def _load_kpi(path: str, mtime: float | None) -> pd.DataFrame | None:
    return pd.read_csv(path) if mtime is not None else None


hist = scope_to_sources(_load_hist(str(history_f), history_f.stat().st_mtime), cfg)
_kpi_rel = _data.get("kpi_path")
_kpi_f = (ROOT / _kpi_rel) if _kpi_rel else None
kpi = _load_kpi(str(_kpi_f) if _kpi_f else "",
                _kpi_f.stat().st_mtime if _kpi_f is not None and _kpi_f.exists() else None)

_dates = pd.to_datetime(hist["date"])
_dr, _chsel = filters.sidebar_filters(
    hist["channel"].dropna().unique(), _dates.min().date(), _dates.max().date())
hist = filters.apply(hist, _dr, _chsel)
if hist.empty:
    st.info("No rows for the current filter — widen the date range or channel selection.")
    st.stop()

geo = drilldown.geo_summary(hist, kpi=kpi, populations=POPS)
if geo.empty or not geo["key_events"].notna().any():
    st.info("No geo-grained measured outcomes in the current selection.")
    st.stop()

# --- headline ------------------------------------------------------------------------
_top = geo.iloc[0]
_has_idx = "vs_population" in geo.columns and geo["vs_population"].notna().any()
if _has_idx:
    _hot = geo.loc[geo["vs_population"].idxmax()]
    theme.action_title(
        f"{_geo_label(_top['geo'])} delivers the most {KPI} — "
        f"{_geo_label(_hot['geo'])} over-indexes hardest vs its population "
        f"({_hot['vs_population']:.2f}x)",
        "Map + table are GA4-measured starts across all traffic; the index compares "
        "each region's share of starts to its share of population (1.00x = proportional).")
else:
    theme.action_title(f"{_geo_label(_top['geo'])} delivers the most {KPI}")

# --- the national heat map -----------------------------------------------------------
_MAP_METRICS = {
    f"{KPI.capitalize()}": ("key_events", "count"),
    "Vs population (index)": ("vs_population", "index"),
}
if not _has_idx:
    _MAP_METRICS.pop("Vs population (index)")

if GEO_STATES:
    _pick = st.pills("Colour the map by", list(_MAP_METRICS), selection_mode="single",
                     default=list(_MAP_METRICS)[0], key="_geo_metric",
                     label_visibility="collapsed") or list(_MAP_METRICS)[0]
    _col, _kind = _MAP_METRICS[_pick]

    # paint each region's value onto its member states; a state claimed by two
    # regions (CA) carries their combined value and both names in the hover
    vals = geo.set_index("geo")[_col].to_dict()
    state_val: dict[str, float] = {}
    state_regions: dict[str, list[str]] = {}
    for code, states in GEO_STATES.items():
        for s in states or []:
            state_regions.setdefault(s, []).append(code)
    rows = []
    for s, codes in state_regions.items():
        vs = [float(vals.get(c)) for c in codes
              if vals.get(c) is not None and pd.notna(vals.get(c))]
        if not vs:
            continue
        v = sum(vs)
        if _kind == "index" and len(vs) > 1:      # an index averages, it never sums
            v = sum(vs) / len(vs)
        label = " + ".join(_geo_label(c) for c in codes)
        note = " (combined for state rendering)" if len(codes) > 1 else ""
        txt = (f"{label}{note}<br>{v:,.0f} {KPI}" if _kind == "count"
               else f"{label}{note}<br>{v:.2f}x vs population share")
        rows.append((s, v, txt))
    _mapdf = pd.DataFrame(rows, columns=["state", "value", "text"])
    fig = go.Figure(go.Choropleth(
        locations=_mapdf["state"], z=_mapdf["value"], locationmode="USA-states",
        colorscale=[[0.0, "#EAF0F6"], [1.0, theme.ACCENT]],
        marker_line_color="white", marker_line_width=1.0,
        text=_mapdf["text"], hoverinfo="text",
        colorbar=dict(thickness=10, len=0.7, tickfont=dict(size=11,
                                                           color=theme.INK_SOFT))))
    fig.update_geos(scope="usa", bgcolor="rgba(0,0,0,0)",
                    lakecolor="rgba(0,0,0,0)", showlakes=False)
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=430,
                      paper_bgcolor="rgba(0,0,0,0)",
                      font=dict(family=theme.SANS, size=13, color=theme.INK))
    st.plotly_chart(fig, use_container_width=True, theme=None,
                    config={"displayModeBar": False})
    st.caption("States are painted with their **field-office region's** value (the "
               "regions are the reporting grain, not states). California carries "
               "Pacific Southwest + Northern California combined — a state can't be "
               "split on a state-level map.")
else:
    ranked = geo.sort_values("key_events")
    fig = go.Figure(go.Bar(
        y=[_geo_label(g) for g in ranked["geo"]], x=ranked["key_events"],
        orientation="h", marker_color=theme.MEASURED,
        text=[f"{v:,.0f}" for v in ranked["key_events"]], textposition="outside"))
    fig.update_xaxes(range=[0, float(ranked["key_events"].max()) * 1.2])
    theme.plotly_chart(fig, xfmt="count", height=80 + 40 * len(ranked), legend=False)
    st.caption("Set `data.geo_states` in config (region → member states) to render "
               "the national heat map.")

# --- the per-region table --------------------------------------------------------------
st.divider()
theme.action_title("Region by region",
                   f"{KPI.capitalize()} are GA4-measured across all traffic; submitted "
                   "applications are the CRM matchback; spend is paid media.")
tbl = geo.copy()
tbl.insert(0, "region", tbl["geo"].map(_geo_label))
_colcfg = {
    "region": st.column_config.TextColumn("Region"),
    "geo": st.column_config.TextColumn("Code"),
    "key_events": st.column_config.NumberColumn(KPI.capitalize(), format="%,.0f"),
    "start_share": st.column_config.NumberColumn("Share", format="percent"),
    "submitted_applications": st.column_config.NumberColumn("Submitted apps (CRM)",
                                                            format="%,.0f"),
    "spend": st.column_config.NumberColumn("Paid spend", format="$%,.0f"),
    "pop_share": st.column_config.NumberColumn("Population share", format="percent"),
    "vs_population": st.column_config.NumberColumn("Vs population", format="%.2fx"),
}
st.dataframe(tbl, use_container_width=True, hide_index=True,
             column_config={k: v for k, v in _colcfg.items() if k in tbl.columns})
if _has_idx:
    st.caption("**Vs population** = share of starts ÷ share of population — an "
               "over/under-index of where applicants come from, not a media verdict. "
               "Mid-Atlantic's pull reflects HQ gravity as much as any campaign.")
st.download_button("Download region table (CSV)",
                   tbl.to_csv(index=False).encode("utf-8"),
                   "geography.csv", "text/csv")
