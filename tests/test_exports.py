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
    assert exports.detect_format(inbox / "google_ads_adgroup_report.csv") \
        == exports.GOOGLE_ADGROUPS
    assert exports.detect_format(inbox / "meta_ads_adset_export.csv") == exports.META_ADSETS
    assert exports.detect_format(inbox / "linkedin_creative_export.csv") \
        == exports.LINKEDIN_CREATIVES
    assert exports.detect_format(inbox / "README.md") is None


def test_ad_level_formats_file_under_the_platform_source(inbox):
    for fname, source in [("google_ads_adgroup_report.csv", exports.GOOGLE),
                          ("meta_ads_adset_export.csv", exports.META),
                          ("linkedin_creative_export.csv", exports.LINKEDIN)]:
        got_source, _df = exports.read_export(inbox / fname)
        assert got_source == source


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


def _truth_ad_groups(truth, campaign):
    return truth["by_ad_group"][campaign]


def test_google_adgroup_reader_decodes_and_reconciles(inbox, truth):
    df = exports.read_google_adgroup_export(inbox / "google_ads_adgroup_report.csv")
    assert (df["ad_group"] != "").all()
    assert set(df["channel"].unique()) == {"google_search", "google_demandgen"}
    # ad-level spend reconciles to the campaign-level truth to the cent
    for camp, groups in truth["by_ad_group"].items():
        if not camp.startswith(("MBA_Search", "MBA_DemandGen")):
            continue
        for gname, t in groups.items():
            got = df.loc[df["ad_group"] == gname, "spend"].sum()
            assert got == pytest.approx(t["spend"], abs=0.05)
            if t["intended_decode"]:
                sub = df[df["ad_group"] == gname]
                for field, val in t["intended_decode"].items():
                    assert (sub[field] == val).all()
            else:
                assert (df.loc[df["ad_group"] == gname, "audience_type"]
                        == "(unparsed)").all()


def test_meta_adset_reader_decodes_audiences(inbox, truth):
    df = exports.read_meta_export(inbox / "meta_ads_adset_export.csv")
    assert (df["currency"] == "USD").all()
    assert (df["ad_group"] != "").all()
    assert {"PROSPECT", "RETARGET", "(unparsed)"} == set(df["audience_type"].unique())
    # the non-conforming ad set is bucketed, not guessed
    unp = df[df["audience_type"] == "(unparsed)"]
    assert set(unp["ad_group"].unique()) == {"Advantage+ broad (test)"}
    total = sum(t["spend"] for g in ("MBA_Meta_Prospecting", "MBA_Meta_Retargeting")
                for t in _truth_ad_groups(truth, g).values())
    assert df["spend"].sum() == pytest.approx(total, abs=0.10)


def test_linkedin_creative_reader_decodes_creatives(inbox, truth):
    df = exports.read_linkedin_creative_export(inbox / "linkedin_creative_export.csv")
    assert not df["date"].isna().any()
    assert (df["account_id"] == "507123456").all()
    assert (df["ad_group"] != "").all()
    # creative names decode to creative fields; audience fields stay empty (honesty:
    # LinkedIn's sub-campaign entity is the creative, audience stays campaign-level)
    assert {"BRANDHERO", "TESTIMONIAL", "DEADLINE"} == set(df["creative"].unique())
    assert set(df["creative_format"].unique()) == {"VID", "STATIC"}
    assert (df["audience_type"] == "").all()


def test_mixed_grain_ingest_does_not_double_count(inbox, tmp_path, truth):
    """Campaign-level AND ad-level exports for the same platform in one inbox: the
    store keeps the ad-level rows and drops their campaign-level aggregate."""
    raw = tmp_path / "raw"
    for fname in ("meta_ads_export.csv", "meta_ads_adset_export.csv"):
        source, df = exports.read_export(inbox / fname)
        store.write_pull(df, source, raw_root=raw)
    manifest = store.consolidate(raw_root=raw, history_path=tmp_path / "history.parquet",
                                 manifest_path=tmp_path / "manifest.json")
    hist = pd.read_parquet(tmp_path / "history.parquet")
    assert manifest["superseded_campaign_rows"] > 0
    assert (hist["ad_group"] != "").all()          # only the finer grain survived
    assert hist["spend"].sum() == pytest.approx(
        truth["by_channel"]["meta"]["total_spend"], abs=1.0)


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


def test_inbox_to_store_to_weekly_end_to_end(inbox, tmp_path, truth):
    raw = tmp_path / "raw"
    for f in sorted(inbox.glob("*.csv")):                # all 7 files: both grains + GA4
        source, df = exports.read_export(f)
        store.write_pull(df, source, raw_root=raw)
    store.consolidate(raw_root=raw, history_path=tmp_path / "history.parquet",
                      manifest_path=tmp_path / "manifest.json")
    hist = pd.read_parquet(tmp_path / "history.parquet")
    assert set(hist["source"].unique()) == {exports.GOOGLE, exports.META,
                                            exports.LINKEDIN, exports.GA4}
    # decoded naming fields ride through the store
    ad_rows = hist[hist["ad_group"] != ""]
    assert len(ad_rows) and (ad_rows["audience_type"] != "").any() \
        and (ad_rows["creative"] != "").any()
    # both grains ingested, yet total spend still matches the truth once — no double count
    truth_spend = sum(ch["total_spend"] for ch in truth["by_channel"].values())
    assert hist["spend"].sum() == pytest.approx(truth_spend, abs=2.0)
    # ad rows and GA4 rows coexist on the same grain and flow through cleaning
    cleaned, _rep = clean_ad_data(hist)
    weekly = to_weekly(cleaned)
    assert weekly["spend"].sum() == pytest.approx(hist["spend"].sum(), rel=1e-6)
    assert len(weekly) > 0
