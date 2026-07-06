"""Drill-down aggregation tests (R3/R4) — incl. the honesty rule: key_events appear
at campaign grain only, never on audience/creative rollups."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from advanced_reporting.dashboard import drilldown
from advanced_reporting.ingestion.naming_decode import UNPARSED


def _hist() -> pd.DataFrame:
    """14 days: meta campaign with 3 ad sets (one unparsed), a LinkedIn creative
    campaign, and campaign-level GA4 rows."""
    dates = pd.date_range("2026-01-05", periods=14, freq="D")
    rows = []
    for d in dates:
        base = {"date": d, "geo": "national", "impressions": 1000.0, "clicks": 30.0,
                "key_events": np.nan}
        rows += [
            {**base, "channel": "meta", "campaign": "C1", "source": "meta_ads",
             "ad_group": "PROSPECT_LAL-1PCT_FEED", "audience_type": "PROSPECT",
             "audience_detail": "LAL-1PCT", "creative": "", "creative_format": "",
             "spend": 60.0, "conversions": 6.0},
            {**base, "channel": "meta", "campaign": "C1", "source": "meta_ads",
             "ad_group": "RETARGET_SITE-90D_FEED", "audience_type": "RETARGET",
             "audience_detail": "SITE-90D", "creative": "", "creative_format": "",
             "spend": 40.0, "conversions": 8.0},
            {**base, "channel": "meta", "campaign": "C1", "source": "meta_ads",
             "ad_group": "old broad (test)", "audience_type": UNPARSED,
             "audience_detail": "", "creative": "", "creative_format": "",
             "spend": 10.0, "conversions": 1.0},
            {**base, "channel": "linkedin", "campaign": "C2", "source": "linkedin_ads",
             "ad_group": "BRANDHERO_VID_1x1_V1", "audience_type": "",
             "audience_detail": "", "creative": "BRANDHERO", "creative_format": "VID",
             "spend": 50.0, "conversions": 2.0},
            # campaign-level GA4 rows: NaN ad metrics, measured key events
            {"date": d, "geo": "national", "channel": "meta", "campaign": "C1",
             "source": "ga4", "ad_group": "", "audience_type": "",
             "audience_detail": "", "creative": "", "creative_format": "",
             "spend": np.nan, "impressions": np.nan, "clicks": np.nan,
             "conversions": np.nan, "key_events": 4.0},
        ]
    return pd.DataFrame(rows)


def test_campaign_table_joins_ga4_key_events():
    per = drilldown.campaign_table(_hist()).set_index(["channel", "campaign"])
    c1 = per.loc[("meta", "C1")]
    assert c1["spend"] == pytest.approx(14 * 110)
    assert c1["conversions"] == pytest.approx(14 * 15)
    assert c1["key_events"] == pytest.approx(14 * 4)          # GA4 joined at this grain
    assert c1["cost_per_key_event"] == pytest.approx(1540 / 56)
    assert np.isnan(per.loc[("linkedin", "C2"), "key_events"])  # no GA4 series there


def test_ad_group_table_is_claimed_only():
    ag = drilldown.ad_group_table(_hist())
    assert "key_events" not in ag.columns          # the honesty rule, structurally
    assert len(ag) == 4
    row = ag.set_index("ad_group").loc["RETARGET_SITE-90D_FEED"]
    assert row["cost_per_claimed"] == pytest.approx(40 / 8)


def test_audience_summary_excludes_creative_only_and_keeps_unparsed():
    aud = drilldown.audience_summary(_hist())
    types = set(aud["audience_type"])
    assert types == {"PROSPECT", "RETARGET", UNPARSED}   # LinkedIn creative rows out
    assert aud["spend_share"].sum() == pytest.approx(1.0)
    assert aud["claimed_share"].sum() == pytest.approx(1.0)
    assert aud.iloc[0]["cost_per_claimed"] <= aud.iloc[-1]["cost_per_claimed"]


def test_creative_summary():
    cre = drilldown.creative_summary(_hist())
    assert len(cre) == 1
    assert cre.iloc[0]["creative"] == "BRANDHERO"
    assert cre.iloc[0]["cost_per_claimed"] == pytest.approx(50 / 2)


def test_audience_weekly_excludes_unparsed_and_snaps_to_monday():
    tr = drilldown.audience_weekly(_hist())
    assert set(tr["audience"]) == {"PROSPECT · LAL-1PCT", "RETARGET · SITE-90D"}
    assert (pd.to_datetime(tr["date"]).dt.weekday == 0).all()


def test_unparsed_stats_by_rows_and_spend():
    unp = drilldown.unparsed_stats(_hist())
    assert unp["row_rate"] == pytest.approx(14 / 56)
    assert unp["spend_rate"] == pytest.approx(140 / 2240)
    assert unp["names"] == ["old broad (test)"]


def test_empty_store_degrades_gracefully():
    empty = _hist().iloc[0:0]
    assert drilldown.ad_group_table(empty).empty
    assert drilldown.audience_summary(empty).empty
    assert drilldown.audience_weekly(empty).empty
    assert drilldown.unparsed_stats(empty)["row_rate"] == 0.0
