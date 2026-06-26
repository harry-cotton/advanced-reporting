import numpy as np
import pandas as pd
import pytest

from advanced_reporting.transform import clean
from advanced_reporting.reporting import metrics as M


def _weekly_fixture() -> pd.DataFrame:
    """One week, two channels, with known totals for hand-checked assertions."""
    return pd.DataFrame({
        "date": pd.to_datetime(["2026-01-05", "2026-01-05"]),
        "channel": ["meta", "tiktok"],
        "spend": [1000.0, 500.0],
        "impressions": [100000.0, 50000.0],
        "clicks": [2000.0, 500.0],
        "conversions": [100.0, 20.0],
        "platform_revenue": [4000.0, 1500.0],
        "sessions": [1600.0, 400.0],
        "engaged_sessions": [800.0, 100.0],
        "page_views": [4800.0, 800.0],
        "video_views": [0.0, 3000.0],
    })


def test_registry_loads_and_is_valid():
    reg = M.load_metric_registry()
    assert reg and all(m["tier"] in M.TIERS for m in reg)
    assert {"ctr", "cpm", "engagement_rate", "cpa", "roas"}.issubset({m["key"] for m in reg})


def test_national_formulas_compute_on_aggregate():
    v = M.compute_metrics(_weekly_fixture()).set_index("metric")["value"]
    assert v["ctr"] == pytest.approx(2500 / 150000)
    assert v["cpm"] == pytest.approx(1500 / 150000 * 1000)
    assert v["engagement_rate"] == pytest.approx(900 / 2000)
    assert v["cpa"] == pytest.approx(1500 / 120)
    assert v["roas"] == pytest.approx(5500 / 1500)
    assert v["conversion_rate"] == pytest.approx(120 / 2000)


def test_ratio_of_sums_not_mean_of_ratios():
    v = M.compute_metrics(_weekly_fixture()).set_index("metric")["value"]
    # per-row CTRs are 0.02 and 0.01; their mean (0.015) is the wrong answer.
    assert v["ctr"] == pytest.approx(2500 / 150000)
    assert v["ctr"] != pytest.approx(0.015)


def test_divide_by_zero_and_missing_engagement_are_nan():
    df = _weekly_fixture()
    df["impressions"] = 0.0
    v = M.compute_metrics(df).set_index("metric")["value"]
    assert np.isnan(v["ctr"]) and np.isnan(v["cpm"])

    df2 = _weekly_fixture().drop(columns=["sessions", "engaged_sessions"])
    v2 = M.compute_metrics(df2).set_index("metric")["value"]
    assert np.isnan(v2["engagement_rate"])        # intent metric -> not measured
    assert not np.isnan(v2["roas"])               # outcome metric still computes


def test_compute_by_channel_groups():
    res = M.compute_metrics(_weekly_fixture(), by="channel")
    assert set(res["key"]) == {"meta", "tiktok"}
    meta_roas = res[(res.key == "meta") & (res.metric == "roas")]["value"].iloc[0]
    assert meta_roas == pytest.approx(4000 / 1000)


def test_by_unknown_column_raises():
    with pytest.raises(KeyError):
        M.compute_metrics(_weekly_fixture(), by="geo")


def test_goal_resolution_override_inference_default():
    assert M.resolve_goal("retargeting") == "conversion"        # override
    assert M.resolve_goal("tiktok_awareness") == "awareness"    # override
    assert M.resolve_goal("nonbrand") == "conversion"           # override beats 'brand' substr
    assert M.resolve_goal("Spring_Brand_Push") == "awareness"   # inference (brand)
    assert M.resolve_goal("q3_engagement_drive") == "consideration"
    assert M.resolve_goal("zzz_unknown_xyz") == "conversion"    # default


def test_primary_tier_mapping():
    assert M.primary_tier("awareness") == "reach"
    assert M.primary_tier("consideration") == "intent"
    assert M.primary_tier("conversion") == "outcome"


def test_tag_campaign_goals_dedupes_and_maps():
    out = M.tag_campaign_goals(["brand", "retargeting", "linkedin_abm", "brand"])
    assert len(out) == 3
    g = out.set_index("campaign")["goal"]
    assert g["brand"] == "awareness" and g["retargeting"] == "conversion"
    assert out.set_index("campaign")["primary_tier"]["linkedin_abm"] == "intent"


def test_clean_to_weekly_carries_engagement():
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-05", "2026-01-06"]),
        "channel": ["meta", "meta"], "campaign": ["a", "a"], "geo": ["US", "US"],
        "spend": [10.0, 20.0], "impressions": [100.0, 200.0], "clicks": [5.0, 7.0],
        "conversions": [1.0, 2.0], "platform_revenue": [3.0, 4.0],
        "sessions": [4.0, 6.0], "engaged_sessions": [2.0, 3.0],
        "page_views": [12.0, 18.0], "video_views": [0.0, 0.0],
    })
    wk = clean.to_weekly(df)
    assert "sessions" in wk.columns and wk["sessions"].sum() == 10.0
