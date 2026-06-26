import json

import numpy as np
import pandas as pd
import pytest

from advanced_reporting.mmm.transforms import geometric_adstock, hill_saturation
from advanced_reporting.mmm.factory import get_engine
from advanced_reporting.transform import clean
from advanced_reporting.transform.clean import (
    standardize_channel, clean_ad_data, build_modeling_table)
from advanced_reporting.ingestion import schema, connectors, store
from advanced_reporting.ingestion.schema import SchemaError
from advanced_reporting.ingestion.base import DataSource, MissingCredentialsError
from advanced_reporting.ingestion.factory import get_source
from advanced_reporting.ingestion.synthetic import SyntheticSource
from advanced_reporting.ingestion.csv_source import CSVSource
from advanced_reporting.utils import load_mappings


def _store_paths(tmp_path):
    """Temp (raw_root, history_path, manifest_path) for store tests."""
    return (tmp_path / "raw", tmp_path / "history.parquet", tmp_path / "manifest.json")


def _canon_rows(rows):
    """Build a canonical-schema frame from (date, channel, campaign, geo, spend) tuples."""
    df = pd.DataFrame(rows, columns=["date", "channel", "campaign", "geo", "spend"])
    for c in ["impressions", "clicks", "conversions", "platform_revenue"]:
        df[c] = 1.0
    df["currency"] = "USD"
    # normalize fills any optional columns (geo/currency/engagement) the schema may add,
    # so this helper stays valid as the canonical schema grows.
    return schema.normalize(df)[list(schema.CANONICAL_COLUMNS)]


def _ad_df() -> pd.DataFrame:
    """A small synthetic-shaped canonical ad frame (no geo/currency, like the CSV)."""
    return pd.DataFrame({
        "date": ["2024-01-01", "2024-01-08"],
        "channel": ["meta", "tiktok"], "campaign": ["a", "b"],
        "spend": [100.0, 200.0], "impressions": [10, 20], "clicks": [2, 3],
        "conversions": [1, 1], "platform_revenue": [50.0, 60.0],
    })


def test_adstock_conserves_mass_and_carries_forward():
    x = np.array([100.0, 0, 0, 0, 0])
    out = geometric_adstock(x, 0.5, 4)
    assert out[1] > 0                      # spend carries into later weeks
    assert abs(out.sum() - x.sum()) < 1e-6  # normalized weights conserve mass


def test_hill_monotone_and_bounded():
    y = hill_saturation(np.linspace(0, 1000, 50), 200, 1.0)
    assert (np.diff(y) >= -1e-9).all() and y.min() >= 0 and y.max() < 1


def test_standardize_channel_aliases():
    s = pd.Series([" Facebook ", "TIKTOK", "Google Search", "meta"])
    assert standardize_channel(s).tolist() == ["meta", "tiktok", "google_search", "meta"]


def test_clean_removes_dupes_and_negatives():
    df = pd.DataFrame({
        "date": ["2024-01-01", "2024-01-01", "not-a-date"],
        "channel": ["meta", "meta", "meta"], "campaign": ["a", "a", "a"],
        "spend": [100.0, -5, 10], "impressions": [1, 1, 1], "clicks": [0, 0, 0],
        "conversions": [0, 0, 0], "platform_revenue": [0, 0, 0]})
    df = pd.concat([df, df.iloc[[0]]], ignore_index=True)  # exact duplicate
    clean, rep = clean_ad_data(df)
    assert rep["duplicates_removed"] >= 1
    assert rep["bad_dates_dropped"] == 1
    assert (clean["spend"] >= 0).all()


def test_build_modeling_table_fills_missing_channels():
    weekly = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-01-01"]),
        "channel": ["meta", "tiktok"], "spend": [100.0, 200], "impressions": [0, 0],
        "clicks": [0, 0], "conversions": [0, 0], "platform_revenue": [0, 0]})
    kpi = pd.DataFrame({"date": pd.to_datetime(["2024-01-01"]), "revenue": [1000.0],
                        "price_index": [100.0], "promo_flag": [0]})
    mt = build_modeling_table(weekly, kpi, ["meta", "tiktok", "google_search"],
                              ["price_index", "promo_flag"], "revenue")
    assert {"meta", "tiktok", "google_search", "revenue"}.issubset(mt.columns)
    assert mt["google_search"].iloc[0] == 0.0


def test_baseline_recovers_signal_with_ordered_ci():
    rng = np.random.default_rng(0)
    n = 90
    t = np.arange(n)
    a = 20000 + 9000 * np.sin(2 * np.pi * t / 52) + rng.normal(0, 2500, n)
    b = 15000 + rng.normal(0, 1500, n)
    contrib_a = 350000 * hill_saturation(geometric_adstock(a, 0.4), np.median(a), 1.0)
    y = 200000 + contrib_a + rng.normal(0, 6000, n)
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="W-MON"),
        "chan_a": a, "chan_b": b,
        "price_index": 100 + rng.normal(0, 2, n),
        "promo_flag": (rng.random(n) < 0.1).astype(int), "revenue": y})
    res = get_engine("baseline", n_boot=60).fit(
        df, ["chan_a", "chan_b"], ["price_index", "promo_flag"], "revenue", "date")
    assert res.fit_metrics["r2"] > 0.6
    s = res.channel_summary.set_index("channel")
    assert s.loc["chan_a", "contribution"] > s.loc["chan_b", "contribution"]
    assert s.loc["chan_a", "roi_low"] <= s.loc["chan_a", "roi"] <= s.loc["chan_a", "roi_high"] + 1e-6


# --- Phase-2 canonical schema + config-driven mappings -------------------------------

def test_schema_validate_missing_required_raises():
    df = _ad_df().drop(columns=["spend"])
    with pytest.raises(SchemaError) as exc:
        schema.validate(df)
    assert "spend" in str(exc.value)


def test_schema_normalize_defaults_geo_and_currency():
    out = schema.normalize(_ad_df())
    assert (out["geo"] == "national").all()
    assert (out["currency"] == "USD").all()
    assert out["spend"].tolist() == [100.0, 200.0]          # metric values untouched
    assert list(out.columns) == list(schema.CANONICAL_COLUMNS)  # canonical order


def test_schema_normalize_currency_override():
    out = schema.normalize(_ad_df(), currency="GBP")
    assert (out["currency"] == "GBP").all()


def test_schema_dtype_coercion():
    df = _ad_df()
    df["spend"] = df["spend"].astype(str)                   # "100.0" etc.
    out = schema.normalize(df, coerce_dtypes=True)
    assert out["spend"].dtype == "float64"
    assert str(out["date"].dtype).startswith("datetime64")
    assert out["spend"].tolist() == [100.0, 200.0]


def test_apply_source_map_renames_to_canonical():
    raw = pd.DataFrame({
        "Day": ["2024-01-01"], "Campaign": ["brand"], "Cost": [123.0],
        "Impr.": [1000], "Clicks": [50], "Conversions": [5], "Conv. value": [400.0]})
    out = schema.apply_source_map(raw, "google_ads", load_mappings())
    for col in ["date", "campaign", "spend", "impressions",
                "clicks", "conversions", "platform_revenue"]:
        assert col in out.columns


def test_apply_source_map_default_is_identity():
    df = _ad_df()
    out = schema.apply_source_map(df, "default", load_mappings())
    pd.testing.assert_frame_equal(out, df)


def test_apply_source_map_unknown_source_falls_back_to_default():
    df = _ad_df()
    out = schema.apply_source_map(df, "does_not_exist", load_mappings())
    pd.testing.assert_frame_equal(out, df)


def test_to_canonical_roundtrip_identity_for_csv():
    df = _ad_df()
    out = schema.to_canonical(df, "default", load_mappings())
    pd.testing.assert_frame_equal(out[list(df.columns)], df)  # original cols byte-identical
    assert (out["geo"] == "national").all()
    assert (out["currency"] == "USD").all()


def test_channel_aliases_loaded_from_config():
    assert load_mappings()["channel_aliases"] == clean.CHANNEL_ALIASES
    out = standardize_channel(pd.Series([" Facebook ", "TIKTOK", "Google Search", "meta"]))
    assert out.tolist() == ["meta", "tiktok", "google_search", "meta"]


# --- Phase-2 extraction layer: SyntheticSource / registry / connectors ----------------

def test_synthetic_source_yields_canonical_schema():
    df = SyntheticSource(geos=["US-NE", "US-MW"]).fetch()
    schema.validate(df)                                      # required cols present
    assert list(df.columns) == list(schema.CANONICAL_COLUMNS)
    assert df["geo"].nunique() == 2 and not df.empty
    # daily grain & messy: cleanses down to the 5 canonical channels
    clean_df, rep = clean_ad_data(df)
    assert set(clean_df["channel"]).issubset(
        {"google_search", "google_pmax", "meta", "tiktok", "linkedin"})
    assert rep["rows_in"] > rep["rows_out"]                  # dupes were injected + removed


def test_synthetic_source_deterministic():
    a = SyntheticSource(geos=["US-NE"], seed=7).fetch()
    b = SyntheticSource(geos=["US-NE"], seed=7).fetch()
    pd.testing.assert_frame_equal(a, b)


def test_synthetic_source_date_range_filter():
    df = SyntheticSource(geos=["US-NE"]).fetch(start="2026-01-01", end="2026-03-31")
    d = pd.to_datetime(df["date"])
    assert d.min() >= pd.Timestamp("2026-01-01") and d.max() <= pd.Timestamp("2026-03-31")
    assert not df.empty


def test_get_source_resolves_each():
    expected = {
        "synthetic": SyntheticSource, "csv": None, "supermetrics": None,
        "google_ads": connectors.GoogleAdsSource, "meta": connectors.MetaSource,
        "tiktok": connectors.TikTokSource, "linkedin": connectors.LinkedInSource,
    }
    for name, cls in expected.items():
        kwargs = {"path": "x.csv"} if name == "csv" else {}
        src = get_source(name, **kwargs)
        assert isinstance(src, DataSource)
        if cls is not None:
            assert isinstance(src, cls)


def test_get_source_unknown_raises():
    with pytest.raises(ValueError, match="Unknown data source"):
        get_source("bogus")


@pytest.mark.parametrize("cls", [
    connectors.GoogleAdsSource, connectors.MetaSource,
    connectors.TikTokSource, connectors.LinkedInSource])
def test_connector_skeletons_raise_not_implemented(cls):
    with pytest.raises(NotImplementedError):
        cls().fetch("2026-01-01", "2026-01-31")


def test_require_credentials_missing_raises():
    with pytest.raises(MissingCredentialsError, match="DEFINITELY_MISSING_KEY"):
        SyntheticSource().require_credentials("DEFINITELY_MISSING_KEY")


# --- Phase-2 durable raw store: dedupe / incrementality / idempotency -----------------

def test_store_dedupes_on_grain_key_latest_wins(tmp_path):
    raw, hist, man = _store_paths(tmp_path)
    key = ("2025-01-06", "meta", "prospecting", "US-NE")
    store.write_pull(_canon_rows([(*key, 100.0)]), "synthetic", raw_root=raw, stamp="20250101")
    store.write_pull(_canon_rows([(*key, 250.0)]), "synthetic", raw_root=raw, stamp="20250102")
    m = store.consolidate(raw_root=raw, history_path=hist, manifest_path=man)
    h = store.read_history(hist)
    assert len(h) == 1 and m["history_rows"] == 1           # one row per grain key
    assert h["spend"].iloc[0] == 250.0                      # latest pull wins
    assert not h.duplicated(subset=list(store.KEY_COLS)).any()


def test_store_incremental_accumulates(tmp_path):
    raw, hist, man = _store_paths(tmp_path)
    store.write_pull(_canon_rows([("2025-01-06", "meta", "p", "US-NE", 10.0)]),
                     "synthetic", raw_root=raw, stamp="20250101")
    store.consolidate(raw_root=raw, history_path=hist, manifest_path=man)
    store.write_pull(_canon_rows([("2025-02-03", "tiktok", "a", "US-W", 20.0)]),
                     "synthetic", raw_root=raw, stamp="20250201")
    store.consolidate(raw_root=raw, history_path=hist, manifest_path=man)
    h = store.read_history(hist)
    assert len(h) == 2                                      # prior history preserved + new added
    assert set(pd.to_datetime(h["date"]).dt.strftime("%Y-%m-%d")) == {"2025-01-06", "2025-02-03"}


def test_store_idempotent_reconsolidate(tmp_path):
    raw, hist, man = _store_paths(tmp_path)
    store.write_pull(_canon_rows([("2025-01-06", "meta", "p", "US-NE", 10.0),
                                  ("2025-01-06", "tiktok", "a", "US-W", 20.0)]),
                     "synthetic", raw_root=raw, stamp="20250101")
    m1 = store.consolidate(raw_root=raw, history_path=hist, manifest_path=man)
    h1 = store.read_history(hist)
    m2 = store.consolidate(raw_root=raw, history_path=hist, manifest_path=man)
    h2 = store.read_history(hist)
    pd.testing.assert_frame_equal(h1, h2)                   # re-consolidation is identical
    assert m1["history_rows"] == m2["history_rows"] == 2


def test_write_pull_never_overwrites(tmp_path):
    raw, _, _ = _store_paths(tmp_path)
    p1 = store.write_pull(_canon_rows([("2025-01-06", "meta", "p", "US-NE", 1.0)]),
                          "synthetic", raw_root=raw, stamp="20250101")
    p2 = store.write_pull(_canon_rows([("2025-01-06", "meta", "p", "US-NE", 2.0)]),
                          "synthetic", raw_root=raw, stamp="20250101")
    assert p1 != p2 and p1.exists() and p2.exists()         # prior pull preserved


def test_synthetic_pull_consolidates_to_grain(tmp_path):
    raw, hist, man = _store_paths(tmp_path)
    df = SyntheticSource(geos=["US-NE", "US-MW"]).fetch(start="2026-01-01", end="2026-03-31")
    store.write_pull(df, "synthetic", raw_root=raw, stamp="20260401")
    store.consolidate(raw_root=raw, history_path=hist, manifest_path=man)
    h = store.read_history(hist)
    assert not h.empty and not h.duplicated(subset=list(store.KEY_COLS)).any()
    d = pd.to_datetime(h["date"])
    assert d.min() >= pd.Timestamp("2026-01-01") and d.max() <= pd.Timestamp("2026-03-31")
    clean_df, _ = clean_ad_data(h)                          # value-level messiness survived the store
    assert set(clean_df["channel"]).issubset(
        {"google_search", "google_pmax", "meta", "tiktok", "linkedin"})


# --- Phase-2 production cleansing layer: rules, gap-fill, anomalies, DQ ----------------

def test_clean_coerces_types_via_schema():
    df = _ad_df()
    df["spend"] = df["spend"].astype(str)                   # "100.0", "200.0"
    df["date"] = df["date"].astype(str)
    cleaned, _ = clean_ad_data(df)
    assert cleaned["spend"].dtype == "float64"
    assert str(cleaned["date"].dtype).startswith("datetime64")
    assert sorted(cleaned["spend"].tolist()) == [100.0, 200.0]


def test_clean_fills_missing_and_clips_negatives():
    df = _ad_df()
    df.loc[0, "spend"] = np.nan
    df.loc[1, "spend"] = -50.0
    cleaned, rep = clean_ad_data(df)
    assert (cleaned["spend"] >= 0).all()
    assert rep["missing_values_filled"] >= 1 and rep["negatives_clipped"] >= 1


def test_fill_calendar_inserts_missing_weeks():
    weekly = _canon_rows([("2025-01-06", "meta", "p", "US-NE", 100.0),
                          ("2025-01-20", "meta", "p", "US-NE", 300.0)]).drop(columns=["campaign"])
    weekly["date"] = pd.to_datetime(weekly["date"])
    filled = clean.fill_calendar(weekly, ["channel", "geo"], freq="W-MON")
    assert len(filled) == 3                                  # 01-13 inserted
    gap = filled[filled["date"] == pd.Timestamp("2025-01-13")]
    assert len(gap) == 1 and gap["spend"].iloc[0] == 0.0
    assert filled[filled["date"] == pd.Timestamp("2025-01-06")]["spend"].iloc[0] == 100.0


def test_to_weekly_geo_keeps_geo_and_sums_days():
    df = _canon_rows([("2025-01-06", "meta", "p", "US-NE", 100.0),
                      ("2025-01-07", "meta", "p", "US-NE", 50.0),
                      ("2025-01-06", "meta", "p", "US-MW", 10.0)])
    df["date"] = pd.to_datetime(df["date"])
    wg = clean.to_weekly_geo(df)
    assert {"date", "channel", "geo"}.issubset(wg.columns)
    assert (wg["date"] == pd.Timestamp("2025-01-06")).all()   # Monday-anchored
    assert wg[wg["geo"] == "US-NE"]["spend"].iloc[0] == 150.0  # days summed within week
    assert clean.to_weekly(df)["spend"].iloc[0] == 160.0       # geos summed for national


def test_dq_flags_spend_spike():
    df = _canon_rows([("2025-01-06", "meta", "p", "US-NE", 100.0),
                      ("2025-01-13", "meta", "p", "US-NE", 500.0)])
    df["date"] = pd.to_datetime(df["date"])
    cleaned, rep = clean_ad_data(df)
    dq = clean.data_quality_report(df, cleaned, rep, spike_factor=3.0)
    spikes = dq["anomalies"]["spend_spikes"]
    assert any(s["channel"] == "meta" and s["ratio"] >= 5.0 for s in spikes)


def test_dq_flags_zero_spend_week():
    df = _canon_rows([("2025-01-06", "meta", "p", "US-NE", 100.0),
                      ("2025-01-20", "meta", "p", "US-NE", 200.0)])   # 01-13 missing
    df["date"] = pd.to_datetime(df["date"])
    cleaned, rep = clean_ad_data(df)
    dq = clean.data_quality_report(df, cleaned, rep)
    assert any(z["week"] == "2025-01-13" for z in dq["anomalies"]["zero_spend_weeks"])
    assert any(g["weeks_missing"] >= 1 for g in dq["coverage_gaps"])


def test_dq_flags_mixed_currency():
    df = _canon_rows([("2025-01-06", "meta", "p", "US-NE", 100.0),
                      ("2025-01-06", "meta", "p", "UK", 100.0)])
    df.loc[1, "currency"] = "GBP"
    cleaned, rep = clean_ad_data(df)
    dq = clean.data_quality_report(df, cleaned, rep)
    assert dq["currency"]["mixed"] is True
    assert set(dq["currency"]["values"]) == {"USD", "GBP"}


def test_dq_report_has_coverage_and_pct_missing():
    df = _canon_rows([("2025-01-06", "meta", "p", "US-NE", 100.0),
                      ("2025-01-13", "meta", "p", "US-NE", 200.0)])
    df.loc[0, "spend"] = np.nan
    cleaned, rep = clean_ad_data(df)
    dq = clean.data_quality_report(df, cleaned, rep)
    assert dq["pct_missing_per_column"]["spend"] == 50.0
    assert dq["date_coverage"]["n_weeks"] >= 1
    assert isinstance(clean.data_quality_markdown(dq), str)


def test_clean_smoke_synthetic_and_csv_yield_valid_canonical(tmp_path):
    syn = SyntheticSource(geos=["US-NE", "US-MW"]).fetch(start="2026-01-01", end="2026-03-31")
    sc, _ = clean_ad_data(syn)
    schema.validate(sc)                                      # canonical after clean
    assert {"date", "channel", "geo"}.issubset(clean.to_weekly_geo(sc).columns)
    assert {"date", "channel"}.issubset(clean.to_weekly(sc).columns)
    p = tmp_path / "ad.csv"
    syn.to_csv(p, index=False)
    cc, _ = clean_ad_data(CSVSource(p, "ad").fetch())
    schema.validate(cc)


# --- Phase-3 prep: mid-funnel engagement columns + GA4 source --------------------------

ENGAGEMENT_COLS = ["sessions", "engaged_sessions", "page_views", "video_views",
                   "avg_engagement_seconds"]


def test_schema_engagement_columns_are_optional():
    for c in ENGAGEMENT_COLS:
        assert c in schema.OPTIONAL_COLUMNS and c not in schema.REQUIRED_COLUMNS
    schema.validate(_ad_df())                               # existing ad source still passes
    out = schema.normalize(_ad_df())                        # added as NaN, canonical order
    assert list(out.columns) == list(schema.CANONICAL_COLUMNS)
    assert out[ENGAGEMENT_COLS].isna().all().all()


def test_synthetic_source_emits_engagement():
    df = SyntheticSource(geos=["US-NE"]).fetch(start="2026-01-01", end="2026-03-31")
    for c in ENGAGEMENT_COLS:
        assert c in df.columns
    eng = df[df["sessions"].notna()]
    assert (eng["sessions"] >= 0).all() and eng["sessions"].sum() > 0
    assert eng["sessions"].sum() < eng["clicks"].sum()     # landing rate < 1 in aggregate
    assert (eng["engaged_sessions"] <= eng["sessions"] + 1).all()  # engaged are a subset
    # video-ish channels have video views; search effectively none
    by_ch = df.groupby("channel")["video_views"].sum()
    assert by_ch.get("tiktok", 0) > 0 and by_ch.get("google_search", 0) == 0


def test_ga4_map_round_trips_through_to_canonical():
    raw = pd.DataFrame({
        "date": ["2025-01-06"],
        "sessionSource": ["google"], "sessionMedium": ["cpc"],
        "sessionCampaignName": ["brand"],
        "sessions": [120.0], "engagedSessions": [80.0], "screenPageViews": [300.0],
        # GA4 carries no ad spend -> present-but-null so the canonical ad schema validates
        "spend": [float("nan")], "impressions": [float("nan")], "clicks": [float("nan")],
        "conversions": [float("nan")], "platform_revenue": [float("nan")],
    })
    out = schema.to_canonical(raw, "ga4", load_mappings())
    assert out["channel"].iloc[0] == "google" and out["campaign"].iloc[0] == "brand"
    assert out["sessions"].iloc[0] == 120.0
    assert out["engaged_sessions"].iloc[0] == 80.0
    assert out["page_views"].iloc[0] == 300.0
    assert {"sessions", "engaged_sessions", "page_views"}.issubset(out.columns)


def test_ga4_source_registered_and_raises():
    src = get_source("ga4")
    assert isinstance(src, DataSource) and isinstance(src, connectors.GA4Source)
    with pytest.raises(NotImplementedError):
        src.fetch("2026-01-01", "2026-01-31")


# --- Schema-aware store: signatures, skip-on-mismatch, archive-only reset --------------

def test_write_pull_writes_schema_sidecar(tmp_path):
    raw, _, _ = _store_paths(tmp_path)
    p = store.write_pull(_canon_rows([("2025-01-06", "meta", "p", "US-NE", 1.0)]),
                         "synthetic", raw_root=raw, stamp="20250101")
    sc = p.with_suffix(".meta.json")
    assert sc.exists()
    meta = json.loads(sc.read_text())
    assert meta["schema_signature"] == schema.schema_signature()
    assert meta["source"] == "synthetic" and meta["rows"] == 1


def test_consolidate_skips_mismatched_schema_pull(tmp_path):
    raw, hist, man = _store_paths(tmp_path)
    store.write_pull(_canon_rows([("2025-01-06", "meta", "p", "US-NE", 100.0)]),
                     "synthetic", raw_root=raw, stamp="20250101")
    bad = store.write_pull(_canon_rows([("2025-01-06", "tiktok", "a", "US-W", 200.0)]),
                           "synthetic", raw_root=raw, stamp="20250102")
    bad.with_suffix(".meta.json").write_text(json.dumps({"schema_signature": "v1:deadbeef"}))
    with pytest.warns(UserWarning, match="Skipping pull"):
        m = store.consolidate(raw_root=raw, history_path=hist, manifest_path=man)
    h = store.read_history(hist)
    assert len(h) == 1 and (h["channel"] == "meta").all()      # mismatched pull excluded
    assert any(s["file"] == bad.name for s in m["skipped_pulls"])
    assert m["schema_signature"] == schema.schema_signature()


def test_consolidate_skips_legacy_no_sidecar_pull(tmp_path):
    raw, hist, man = _store_paths(tmp_path)
    store.write_pull(_canon_rows([("2025-01-06", "meta", "p", "US-NE", 100.0)]),
                     "synthetic", raw_root=raw, stamp="20250101")
    legacy = store.write_pull(_canon_rows([("2025-01-06", "tiktok", "a", "US-W", 5.0)]),
                              "synthetic", raw_root=raw, stamp="20250102")
    legacy.with_suffix(".meta.json").unlink()                  # legacy = no sidecar
    with pytest.warns(UserWarning, match="legacy"):
        m = store.consolidate(raw_root=raw, history_path=hist, manifest_path=man)
    h = store.read_history(hist)
    assert (h["channel"] == "meta").all()
    assert any("legacy" in s["reason"] for s in m["skipped_pulls"])


def test_archive_source_moves_not_deletes(tmp_path):
    raw, _, _ = _store_paths(tmp_path)
    arch = tmp_path / "_arch"
    p1 = store.write_pull(_canon_rows([("2025-01-06", "meta", "p", "US-NE", 1.0)]),
                          "synthetic", raw_root=raw, stamp="20250101")
    p2 = store.write_pull(_canon_rows([("2025-01-13", "meta", "p", "US-NE", 2.0)]),
                          "synthetic", raw_root=raw, stamp="20250102")
    res = store.archive_source("synthetic", raw_root=raw, archive_root=arch, stamp="ts1")
    assert not list((raw / "synthetic").glob("*.csv"))         # source dir emptied
    names = {f.name for f in (arch / "synthetic" / "ts1").glob("*")}
    assert {p1.name, p2.name, p1.with_suffix(".meta.json").name}.issubset(names)  # moved (csv+sidecar)
    assert len(res["moved"]) == 4


def test_reset_then_fresh_pull_single_schema_history(tmp_path):
    raw, hist, man = _store_paths(tmp_path)
    arch = tmp_path / "_arch"
    stale = store.write_pull(_canon_rows([("2025-01-06", "META", "p", "US-NE", 9.0)]),
                             "synthetic", raw_root=raw, stamp="20240101")
    stale.with_suffix(".meta.json").unlink()                   # would inflate if it unioned
    store.archive_source("synthetic", raw_root=raw, archive_root=arch, stamp="ts1")  # reset
    store.write_pull(_canon_rows([("2025-01-06", "meta", "p", "US-NE", 100.0)]),
                     "synthetic", raw_root=raw, stamp="20250201")                    # fresh
    m = store.consolidate(raw_root=raw, history_path=hist, manifest_path=man)
    h = store.read_history(hist)
    assert len(h) == 1 and (h["channel"] == "meta").all()      # only the fresh pull
    assert m["skipped_pulls"] == []                            # stale archived, not skipped
    assert (arch / "synthetic" / "ts1" / stale.name).exists()  # archived file remains on disk


def test_consolidate_idempotent_with_sidecars(tmp_path):
    raw, hist, man = _store_paths(tmp_path)
    store.write_pull(_canon_rows([("2025-01-06", "meta", "p", "US-NE", 1.0),
                                  ("2025-01-06", "tiktok", "a", "US-W", 2.0)]),
                     "synthetic", raw_root=raw, stamp="20250101")
    m1 = store.consolidate(raw_root=raw, history_path=hist, manifest_path=man)
    h1 = store.read_history(hist)
    m2 = store.consolidate(raw_root=raw, history_path=hist, manifest_path=man)
    h2 = store.read_history(hist)
    pd.testing.assert_frame_equal(h1, h2)
    assert m1["history_rows"] == m2["history_rows"] == 2
