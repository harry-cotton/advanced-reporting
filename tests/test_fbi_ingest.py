"""P2 ingest tests: the FBI dataset's exports parse through their (extended) readers with
the right channels + geo, the generic campaign-grain CSV ingests, and the geo-grained CRM
KPI aggregates to national in build_modeling_table. Runs on a --mini emit (fast)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from advanced_reporting.ingestion import exports, schema  # noqa: E402
from advanced_reporting.transform.clean import build_modeling_table  # noqa: E402


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    import generate_fbi_campaign as gen
    out = tmp_path_factory.mktemp("mmm") / "MMM Data"
    gen.main(["--mini", "--out", str(out)])
    return out


def _read(dataset, name):
    return exports.read_export(dataset / name)


def test_google_export_splits_search_and_youtube(dataset):
    source, df = _read(dataset, "Ad group report.csv")
    schema.validate(df)
    channels = set(df["channel"].unique())
    # YouTube video campaigns come through the Google reader as `youtube` (not demand gen)
    assert "youtube" in channels and "google_search" in channels
    assert "google_demandgen" not in channels
    assert (df["geo"].str.startswith("US-")).all()          # Region -> geo carried through


def test_meta_and_linkedin_carry_geo(dataset):
    _s, meta = _read(dataset, "Meta Ads Manager - Ad Sets.csv")
    _s2, li = _read(dataset, "LinkedIn Creative Performance.csv")
    for df in (meta, li):
        schema.validate(df)
        assert (df["geo"].str.startswith("US-")).all()
    assert set(meta["channel"].unique()) == {"meta"}
    assert set(li["channel"].unique()) == {"linkedin"}


def test_generic_campaign_export_ingests(dataset):
    source, df = _read(dataset, "campaign_delivery.csv")
    schema.validate(df)
    assert source == "campaign_delivery"
    # the campaign-grain channels active in the mini window (ctv launches mid-2024, after
    # the 16-week mini flight), all with a real geo and spend
    assert {"display", "audio", "jobboards"} <= set(df["channel"].unique())
    assert (df["geo"].str.startswith("US-")).all()
    assert df["spend"].sum() > 0
    assert (df["currency"] == "USD").all()


def test_ga4_export_resolves_new_channels(dataset):
    source, df = _read(dataset, "GA4 Traffic acquisition.csv")
    schema.validate(df)
    assert source == "ga4"
    chans = set(df["channel"].unique())
    # the new GA4 source/medium mappings resolve (no `unmapped:` leakage). ctv is absent in
    # the mini window (its geo-lever launch is mid-2024, after the 16-week mini flight).
    assert not any(str(c).startswith("unmapped:") for c in chans)
    assert {"youtube", "organic_search", "direct"} <= chans
    assert df["key_events"].sum() > 0                        # GA4-measured application starts


def test_all_five_exports_detected(dataset):
    fmts = {f.name: exports.detect_format(f) for f in dataset.glob("*.csv")}
    # the two CRM files are NOT exports (skipped at ingest); the five platform files are
    assert fmts["business_kpi_weekly.csv"] is None
    assert fmts["crm_pipeline_stages.csv"] is None
    assert fmts["campaign_delivery.csv"] == exports.GENERIC_CAMPAIGN
    assert fmts["Ad group report.csv"] == exports.GOOGLE_ADGROUPS


def test_geo_kpi_aggregates_to_national(dataset):
    """The FBI CRM matchback (week x geo submitted apps) aggregates to a national target;
    controls (geo-invariant) collapse to one value per week."""
    kpi = pd.read_csv(dataset / "business_kpi_weekly.csv")
    assert "geo" in kpi.columns and kpi["date"].duplicated().any()
    # a minimal weekly_long: one channel with spend per week
    weeks = sorted(kpi["date"].unique())
    weekly_long = pd.DataFrame({"date": pd.to_datetime(weeks), "channel": "meta",
                                "spend": 1000.0})
    model = build_modeling_table(weekly_long, kpi, ["meta"],
                                 ["unemployment_index", "news_spike_flag"],
                                 target="submitted_applications")
    # national target per week == sum of that week's geo rows
    per_week_sum = kpi.groupby("date")["submitted_applications"].sum()
    assert len(model) == len(weeks)
    assert model["submitted_applications"].sum() == pytest.approx(per_week_sum.sum())
    assert "unemployment_index" in model.columns          # controls survived the aggregation
    # one row per week (no geo duplication after the merge)
    assert not model["date"].duplicated().any()
