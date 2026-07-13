"""Cleanse the consolidated daily history and organize it into modeling tables.

Reads ``data/processed/history.parquet`` (the durable store) and REUSES
``ingestion/schema.py`` for validation and type coercion — this module never re-implements
the canonical contract. It then standardizes channels, fixes missing/negative/duplicate rows,
fills calendar gaps per channel x geo, and emits both a national weekly modeling table and a
geo x weekly table, plus a structured data-quality report.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

from ..utils import load_mappings
from ..ingestion import schema, store

METRIC_COLS = ["spend", "impressions", "clicks", "conversions", "platform_revenue"]

# Summable extra columns carried through the weekly tables when present: the mid-funnel
# engagement tier + GA4-measured key_events (avg_engagement_seconds is a per-session
# average, not a sum, so it is intentionally excluded). Absent on pure ad CSVs.
ENGAGEMENT_COLS = ["sessions", "engaged_sessions", "page_views", "video_views", "key_events"]

# Kept as a fallback so standardize_channel still works if config/mappings.yaml is
# missing or unreadable. Must stay in sync with mappings.yaml's channel_aliases.
_FALLBACK_CHANNEL_ALIASES = {
    "facebook": "meta", "fb": "meta", "instagram": "meta", "ig": "meta",
    "google": "google_search", "search": "google_search", "google_search": "google_search",
    "pmax": "google_pmax", "performance_max": "google_pmax", "google_pmax": "google_pmax",
    "tik_tok": "tiktok", "tik-tok": "tiktok", "tiktok": "tiktok",
    "linked_in": "linkedin", "linkedin": "linkedin", "meta": "meta",
}


def _load_channel_aliases() -> dict:
    """Channel aliases from config/mappings.yaml, falling back to the literal above."""
    try:
        aliases = load_mappings().get("channel_aliases")
        return dict(aliases) if aliases else dict(_FALLBACK_CHANNEL_ALIASES)
    except Exception:
        return dict(_FALLBACK_CHANNEL_ALIASES)


# Config-driven; identical contents to the fallback, so behavior is unchanged.
CHANNEL_ALIASES = _load_channel_aliases()


def standardize_channel(s: pd.Series) -> pd.Series:
    norm = (s.astype(str).str.strip().str.lower()
            .str.replace(r"\s+", "_", regex=True))
    return norm.map(lambda v: CHANNEL_ALIASES.get(v, v))


def load_history(history_path=None) -> pd.DataFrame:
    """Read the consolidated daily history (parquet) and schema-validate it."""
    return schema.validate(store.read_history(history_path))


def clean_ad_data(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Validate + coerce (via schema), standardize channels, fix negatives/missing, dedupe."""
    schema.validate(df)                                   # reuse schema: required cols present
    df = schema.normalize(df, coerce_dtypes=True)         # reuse schema: dtype coercion + defaults
    rows_in = len(df)
    df["channel"] = standardize_channel(df["channel"])

    # HARD GATE: mixed currencies must never be summed. The data-quality report used to
    # flag 'MIXED' while the pipeline happily added EUR to USD and modeled the result.
    if "currency" in df.columns:
        currencies = sorted(df["currency"].dropna().astype(str).str.strip().unique())
        currencies = [c for c in currencies if c and c.lower() not in ("nan", "<na>")]
        if len(currencies) > 1:
            raise ValueError(
                f"mixed currencies in the data ({', '.join(currencies)}) — convert to one "
                "currency at ingest before cleaning/modeling; refusing to sum across them")

    bad_dates = df["date"].isna().sum()
    df = df.dropna(subset=["date"])
    nans_filled = int(df[METRIC_COLS].isna().sum().sum())
    negatives = int((df[METRIC_COLS] < 0).sum().sum())
    for c in METRIC_COLS:
        df[c] = df[c].fillna(0).clip(lower=0)

    before = len(df)
    df = df.drop_duplicates().reset_index(drop=True)
    report = {
        "rows_in": rows_in, "rows_out": len(df),
        "bad_dates_dropped": int(bad_dates),
        "duplicates_removed": before - len(df),
        "missing_values_filled": nans_filled,
        "negatives_clipped": negatives,
    }
    return df, report


def _weekly_key(dates: pd.Series) -> pd.Series:
    """Snap each date back to the Monday of its week."""
    dates = pd.to_datetime(dates)
    return dates - pd.to_timedelta(dates.dt.weekday, unit="D")


def _weekly_agg(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    """Sum metrics to weekly (Monday) grain over date + group_cols. No gap-filling.

    Engagement columns are summed alongside the ad metrics when present, so the weekly
    tables carry the mid-funnel tier through to reporting; they are simply absent for
    pure ad-CSV inputs that never measured them.
    """
    df = df.copy()
    df["date"] = _weekly_key(df["date"])
    keys = ["date"] + list(group_cols)
    sum_cols = METRIC_COLS + [c for c in ENGAGEMENT_COLS if c in df.columns]
    return (df.groupby(keys, as_index=False)[sum_cols].sum()
              .sort_values(keys).reset_index(drop=True))


def fill_calendar(df: pd.DataFrame, group_cols: list[str], *, freq: str,
                  date_col: str = "date", metric_cols=None) -> pd.DataFrame:
    """Reindex each group to a regular calendar over the global [min, max] span.

    Missing periods become explicit rows with 0 metrics, so each ``group_cols`` series is a
    gap-free time series (needed for honest week-over-week metrics and anomaly detection).
    """
    metric_cols = list(metric_cols) if metric_cols is not None else list(METRIC_COLS)
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    if df.empty:
        return df
    full = pd.date_range(df[date_col].min(), df[date_col].max(), freq=freq)
    out = []
    for keys, g in df.groupby(list(group_cols), sort=True):
        keys = keys if isinstance(keys, tuple) else (keys,)
        g = g.set_index(date_col).reindex(full)
        g.index.name = date_col
        for col, val in zip(group_cols, keys):
            g[col] = val
        for c in metric_cols:
            if c in g.columns:
                g[c] = g[c].fillna(0.0)
        if "currency" in g.columns:
            g["currency"] = g["currency"].ffill().bfill()
        out.append(g.reset_index())
    res = pd.concat(out, ignore_index=True)
    return res.sort_values(list(group_cols) + [date_col]).reset_index(drop=True)


def to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """National weekly per-channel table (sum geos + campaigns), gap-filled per channel."""
    weekly = _weekly_agg(df, ["channel"])
    cols = METRIC_COLS + [c for c in ENGAGEMENT_COLS if c in weekly.columns]
    return fill_calendar(weekly, ["channel"], freq="W-MON", metric_cols=cols)


def to_weekly_geo(df: pd.DataFrame) -> pd.DataFrame:
    """Geo x weekly per-channel table (LONG), gap-filled per channel x geo.

    One row per (date, channel, geo) with all metrics — raw material for a future
    geo-level MMM. National ``to_weekly`` equals this summed over geo.
    """
    weekly = _weekly_agg(df, ["channel", "geo"])
    cols = METRIC_COLS + [c for c in ENGAGEMENT_COLS if c in weekly.columns]
    return fill_calendar(weekly, ["channel", "geo"], freq="W-MON", metric_cols=cols)


def channel_metrics(weekly_long: pd.DataFrame) -> pd.DataFrame:
    """Add standard performance metrics for the dashboard."""
    d = weekly_long.copy()
    d["ctr"] = d["clicks"] / d["impressions"].replace(0, np.nan)
    d["cvr"] = d["conversions"] / d["clicks"].replace(0, np.nan)
    d["cpa"] = d["spend"] / d["conversions"].replace(0, np.nan)
    d["cpm"] = d["spend"] / d["impressions"].replace(0, np.nan) * 1000
    d["roas"] = d["platform_revenue"] / d["spend"].replace(0, np.nan)
    return d


def build_modeling_table(weekly_long: pd.DataFrame, kpi: pd.DataFrame,
                         channel_cols: list[str], control_cols: list[str],
                         target: str = "revenue") -> pd.DataFrame:
    """Pivot weekly spend wide by channel and merge the business KPI + controls."""
    wide = (weekly_long.pivot_table(index="date", columns="channel",
                                    values="spend", aggfunc="sum").fillna(0.0))
    for c in channel_cols:
        if c not in wide.columns:
            wide[c] = 0.0
    wide = wide[channel_cols].reset_index()

    kpi = kpi.copy()
    kpi["date"] = pd.to_datetime(kpi["date"])
    # A geo-grained KPI (e.g. the FBI CRM matchback: week x geo submitted applications) is
    # aggregated to national: the target SUMS over geos; controls are geo-invariant, so the
    # per-date value is taken once (mean collapses identical rows). The geo x weekly KPI is
    # kept intact upstream for a future geo-level (Meridian) model.
    present_controls = [c for c in control_cols if c in kpi.columns]
    if "geo" in kpi.columns and kpi["date"].duplicated().any():
        agg = {target: "sum", **{c: "mean" for c in present_controls}}
        kpi = kpi.groupby("date", as_index=False).agg(agg)
    keep = ["date", target] + present_controls
    model = (wide.merge(kpi[keep], on="date", how="inner")
                 .sort_values("date").reset_index(drop=True))
    return model


def build_modeling_table_geo(weekly_geo: pd.DataFrame, kpi: pd.DataFrame,
                             channel_cols: list[str], target: str = "revenue",
                             date_col: str = "date",
                             populations: dict | None = None) -> pd.DataFrame:
    """Geo x weekly wide table for a GEO-LEVEL model (Meridian): one row per (date, geo)
    with per-channel spend + the geo-grained KPI + a per-geo ``population``.

    Cross-geo variation is what identifies effects a national model can't; the geo KPI must
    therefore stay geo-grained (NOT aggregated). Time-only national controls are intentionally
    left out — Meridian rejects controls that don't vary across geos (its per-time knots
    absorb them). ``populations`` maps geo code -> population (defaults to 1.0)."""
    wide = (weekly_geo.pivot_table(index=[date_col, "geo"], columns="channel",
                                   values="spend", aggfunc="sum").fillna(0.0))
    for c in channel_cols:
        if c not in wide.columns:
            wide[c] = 0.0
    wide = wide[channel_cols].reset_index()
    wide[date_col] = pd.to_datetime(wide[date_col])
    kpi = kpi.copy()
    kpi[date_col] = pd.to_datetime(kpi[date_col])
    model = wide.merge(kpi[[date_col, "geo", target]], on=[date_col, "geo"], how="inner")
    pops = populations or {}
    model["population"] = model["geo"].map(pops).fillna(1.0).astype(float)
    return model.sort_values([date_col, "geo"]).reset_index(drop=True)


# --- Data-quality report -------------------------------------------------------------

def _coverage_gaps(weekly_unfilled: pd.DataFrame, freq: str = "W-MON") -> list[dict]:
    """Per channel x geo: weeks missing WITHIN the group's active span (interior gaps)."""
    gaps = []
    for (ch, geo), g in weekly_unfilled.groupby(["channel", "geo"]):
        weeks = pd.to_datetime(g["date"]).sort_values()
        present = set(weeks)
        if len(present) < 2:
            continue
        expected = pd.date_range(weeks.min(), weeks.max(), freq=freq)
        missing = int(len(expected) - len(present))
        if missing > 0:
            gaps.append({"channel": str(ch), "geo": str(geo),
                         "first_week": weeks.min().date().isoformat(),
                         "last_week": weeks.max().date().isoformat(),
                         "weeks_present": int(len(present)), "weeks_missing": missing})
    return gaps


def _spend_spikes(weekly_filled: pd.DataFrame, spike_factor: float) -> list[dict]:
    """Per channel x geo: weeks where spend jumped > spike_factor x the prior week."""
    spikes = []
    for (ch, geo), g in weekly_filled.sort_values("date").groupby(["channel", "geo"]):
        s = g["spend"].to_numpy(dtype=float)
        wk = pd.to_datetime(g["date"]).to_numpy()
        for i in range(1, len(s)):
            if s[i - 1] > 0 and s[i] > spike_factor * s[i - 1]:
                spikes.append({"channel": str(ch), "geo": str(geo),
                               "week": pd.Timestamp(wk[i]).date().isoformat(),
                               "prev_spend": round(float(s[i - 1]), 2),
                               "spend": round(float(s[i]), 2),
                               "ratio": round(float(s[i] / s[i - 1]), 2)})
    return spikes


def _zero_spend_weeks(weekly_filled: pd.DataFrame) -> list[dict]:
    """Per channel x geo: zero-spend weeks INSIDE the active range (otherwise-active channel)."""
    flags = []
    for (ch, geo), g in weekly_filled.sort_values("date").groupby(["channel", "geo"]):
        active = g[g["spend"] > 0]
        if active.empty:
            continue
        lo, hi = active["date"].min(), active["date"].max()
        within = g[(g["date"] >= lo) & (g["date"] <= hi)]
        for wk in within.loc[within["spend"] <= 0, "date"]:
            flags.append({"channel": str(ch), "geo": str(geo),
                          "week": pd.Timestamp(wk).date().isoformat()})
    return flags


def data_quality_report(raw: pd.DataFrame, clean: pd.DataFrame, cleaning_report: dict, *,
                        spike_factor: float = 3.0, fill_freq: str = "W-MON") -> dict:
    """Structured data-quality summary: counts, missingness, coverage, anomalies."""
    report = dict(cleaning_report)

    report["pct_missing_per_column"] = {
        c: round(float(raw[c].isna().mean() * 100), 3) for c in raw.columns
    }

    cur = pd.Series(raw["currency"]) if "currency" in raw.columns else pd.Series(dtype=str)
    currencies = sorted(cur.dropna().astype(str).unique().tolist())
    report["currency"] = {"values": currencies, "mixed": len(currencies) > 1}

    wg = _weekly_agg(clean, ["channel", "geo"])               # unfilled: real coverage
    wg_filled = fill_calendar(wg, ["channel", "geo"], freq=fill_freq)
    wd = pd.to_datetime(wg["date"])
    report["date_coverage"] = {
        "start": wd.min().date().isoformat() if len(wd) else None,
        "end": wd.max().date().isoformat() if len(wd) else None,
        "n_weeks": int(wd.nunique()),
    }
    report["coverage_gaps"] = _coverage_gaps(wg, freq=fill_freq)
    report["spike_factor"] = spike_factor
    report["anomalies"] = {
        "spend_spikes": _spend_spikes(wg_filled, spike_factor),
        "zero_spend_weeks": _zero_spend_weeks(wg_filled),
    }
    return report


def _money(x: float) -> str:
    ax = abs(x)
    if ax >= 1e6:
        return f"${x/1e6:.2f}M"
    if ax >= 1e3:
        return f"${x/1e3:.0f}k"
    return f"${x:,.0f}"


def data_quality_markdown(report: dict) -> str:
    """Render the data-quality report as Markdown (mirrors reporting/commentary.py style)."""
    L = ["# Data Quality Report\n"]
    cov = report.get("date_coverage", {})
    cur = report.get("currency", {})
    L.append(f"_Coverage **{cov.get('start')} -> {cov.get('end')}** "
             f"({cov.get('n_weeks', 0)} weeks) - currency "
             f"{'**MIXED**: ' + ', '.join(cur.get('values', [])) if cur.get('mixed') else 'OK'}._\n")

    L.append("## Cleaning")
    L.append(f"- Rows in -> out: **{report.get('rows_in', 0):,} -> {report.get('rows_out', 0):,}**")
    L.append(f"- Duplicates removed: {report.get('duplicates_removed', 0):,}")
    L.append(f"- Negatives clipped: {report.get('negatives_clipped', 0):,}")
    L.append(f"- Missing values filled: {report.get('missing_values_filled', 0):,}")
    L.append(f"- Undated rows dropped: {report.get('bad_dates_dropped', 0):,}\n")

    L.append("## Missingness (% of raw rows null, per column)")
    for col, pct in report.get("pct_missing_per_column", {}).items():
        L.append(f"- `{col}`: {pct:.2f}%")
    L.append("")

    gaps = report.get("coverage_gaps", [])
    L.append(f"## Coverage gaps ({len(gaps)} channel x geo with interior missing weeks)")
    if gaps:
        L.append("| channel | geo | first | last | present | missing |")
        L.append("|---|---|---|---|---|---|")
        for g in gaps[:20]:
            L.append(f"| {g['channel']} | {g['geo']} | {g['first_week']} | {g['last_week']} "
                     f"| {g['weeks_present']} | {g['weeks_missing']} |")
        if len(gaps) > 20:
            L.append(f"_...and {len(gaps) - 20} more._")
    else:
        L.append("- None — every channel x geo is gap-free within its active range.")
    L.append("")

    an = report.get("anomalies", {})
    spikes, zeros = an.get("spend_spikes", []), an.get("zero_spend_weeks", [])
    L.append("## Anomaly flags")
    L.append(f"**Week-over-week spend spikes > {report.get('spike_factor')}x** "
             f"({len(spikes)} flagged)")
    if spikes:
        L.append("| channel | geo | week | prev | spend | x |")
        L.append("|---|---|---|---|---|---|")
        for s in spikes[:20]:
            L.append(f"| {s['channel']} | {s['geo']} | {s['week']} | {_money(s['prev_spend'])} "
                     f"| {_money(s['spend'])} | {s['ratio']:.1f}x |")
        if len(spikes) > 20:
            L.append(f"_...and {len(spikes) - 20} more._")
    else:
        L.append("- None.")
    L.append(f"\n**Zero-spend weeks for otherwise-active channels** ({len(zeros)} flagged)")
    if zeros:
        for z in zeros[:20]:
            L.append(f"- {z['channel']} / {z['geo']} @ {z['week']}")
        if len(zeros) > 20:
            L.append(f"_...and {len(zeros) - 20} more._")
    else:
        L.append("- None.")
    L.append("")
    return "\n".join(L)
