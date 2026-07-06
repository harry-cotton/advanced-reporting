"""Export-reader tests: the four fixture formats (quirks and all) must land in the
canonical schema with totals matching the generator's ground truth."""
from __future__ import annotations

import json

import pandas as pd
import pytest

import scripts.generate_sample_exports as gen
from advanced_reporting.ingestion import exports, schema, store
from advanced_reporting.transform.clean import clean_ad_data, to_weekly


@pytest.fixture(scope="module")
def inbox(tmp_path_factory, module_mocker=None):
    """Generate the fixture exports into a temp inbox once for all tests here."""
    out = tmp_path_factory.mktemp("inbox")
    orig = gen.INBOX
    gen.INBOX = out
    try:
        gen.main()
    finally:
        gen.INBOX = orig
    return out


@pytest.fixture(scope="module")
def truth(inbox):
    return json.loads((inbox / "_ground_truth.json").read_text(encoding="utf-8"))


def test_detect_format(inbox):
    assert exports.detect_format(inbox / "google_ads_campaign_report.csv") == exports.GOOGLE
    assert exports.detect_format(inbox / "meta_ads_export.csv") == exports.META
    assert exports.detect_format(inbox / "linkedin_campaign_export.csv") == exports.LINKEDIN
    assert exports.detect_format(inbox / "ga4_traffic_acquisition.csv") == exports.GA4
    assert exports.detect_format(inbox / "README.md") is None


def test_google_reader_survives_quirks(inbox, truth):
    df = exports.read_google_ads_export(inbox / "google_ads_campaign_report.csv")
    # totals row dropped, quoted thousands parsed, both campaign types split
    assert set(df["channel"].unique()) == {"google_search", "google_demandgen"}
    assert not df["date"].isna().any()
    for ch in ("google_search", "google_demandgen"):
        got = df.loc[df["channel"] == ch, "spend"].sum()
        assert got == pytest.approx(truth["by_channel"][ch]["total_spend"], abs=1.0)
    assert (df["currency"] == "USD").all()
    assert (df["campaign_id"] != "").all()


def test_meta_reader_parses_currency_from_header(inbox, truth):
    df = exports.read_meta_export(inbox / "meta_ads_export.csv")
    assert (df["channel"] == "meta").all()
    assert (df["currency"] == "USD").all()          # extracted from 'Amount spent (USD)'
    assert df["spend"].sum() == pytest.approx(truth["by_channel"]["meta"]["total_spend"],
                                              abs=1.0)


def test_linkedin_reader_parses_locale_dates_and_preamble(inbox, truth):
    df = exports.read_linkedin_export(inbox / "linkedin_campaign_export.csv")
    assert not df["date"].isna().any()               # 1/5/2026 parsed as M/D/YYYY
    assert df["date"].min() == pd.Timestamp("2026-01-05")
    assert (df["currency"] == "USD").all()           # from the preamble, not a column
    assert (df["account_id"] == "507123456").all()   # from 'Account: ... (507123456)'
    assert df["spend"].sum() == pytest.approx(
        truth["by_channel"]["linkedin"]["total_spend"], abs=1.0)


def test_ga4_reader_channels_and_key_events(inbox, truth):
    df = exports.read_ga4_export(inbox / "ga4_traffic_acquisition.csv")
    # campaign override splits 'google / cpc' into search vs demand gen
    assert {"google_search", "google_demandgen", "meta", "linkedin",
            "organic_search", "direct"} <= set(df["channel"].unique())
    # analytics rows measure no ad delivery and claim no currency
    assert df[list(schema.METRIC_COLUMNS)].isna().all().all()
    assert df["currency"].isna().all()
    # key events land in key_events, NEVER conversions (different attribution systems)
    assert df["key_events"].notna().any() and df["conversions"].isna().all()
    paid = df[~df["channel"].isin(["organic_search", "direct"])]
    total_true = sum(truth["by_channel"][ch]["true_start_applications"]
                     for ch in ("google_search", "google_demandgen", "meta", "linkedin"))
    # ~92% tag capture in the DGP
    assert paid["key_events"].sum() == pytest.approx(total_true * 0.92, rel=0.05)


def test_unmapped_source_medium_fails_loud(inbox, tmp_path):
    bad = tmp_path / "ga4_bad.csv"
    bad.write_text("# GA4\nDate,Session source / medium,Session campaign,Sessions,"
                   "Engaged sessions,Key events\n20260105,tiktok / paid,c,10,5,1\n",
                   encoding="utf-8")
    with pytest.raises(schema.SchemaError, match="tiktok / paid"):
        exports.read_ga4_export(bad)


def test_unrecognized_file_is_refused(tmp_path):
    f = tmp_path / "mystery.csv"
    f.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    with pytest.raises(schema.SchemaError, match="unrecognized"):
        exports.read_export(f)


def test_inbox_to_store_to_weekly_end_to_end(inbox, tmp_path):
    raw = tmp_path / "raw"
    for f in sorted(inbox.glob("*.csv")):
        source, df = exports.read_export(f)
        store.write_pull(df, source, raw_root=raw)
    store.consolidate(raw_root=raw, history_path=tmp_path / "history.parquet",
                      manifest_path=tmp_path / "manifest.json")
    hist = pd.read_parquet(tmp_path / "history.parquet")
    assert set(hist["source"].unique()) == {exports.GOOGLE, exports.META,
                                            exports.LINKEDIN, exports.GA4}
    # ad rows and GA4 rows coexist on the same grain and flow through cleaning
    cleaned, _rep = clean_ad_data(hist)
    weekly = to_weekly(cleaned)
    assert weekly["spend"].sum() == pytest.approx(hist["spend"].sum(), rel=1e-6)
    assert len(weekly) > 0
