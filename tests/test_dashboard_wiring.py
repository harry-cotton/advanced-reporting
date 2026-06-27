"""Dashboard wiring tests — pure metric/funnel/format logic, no Streamlit internals.

These import only reporting/metrics.py (which doesn't import the ingestion/transform
modules), so they run on any supported Python without the full pipeline.
"""
import math

import pandas as pd
import pytest

from advanced_reporting.reporting import metrics as M


def _wk() -> pd.DataFrame:
    return pd.DataFrame({
        "channel": ["meta", "tiktok"],
        "spend": [1000.0, 500.0], "impressions": [100000.0, 50000.0],
        "clicks": [2000.0, 500.0], "conversions": [100.0, 20.0],
        "platform_revenue": [4000.0, 1500.0],
        "sessions": [1600.0, 400.0], "engaged_sessions": [800.0, 100.0],
        "page_views": [4800.0, 800.0], "video_views": [0.0, 3000.0],
    })


def test_funnel_volumes_and_step_rates():
    fn = M.funnel(_wk()).set_index("stage")
    assert fn.loc["impressions", "value"] == 150000
    assert fn.loc["clicks", "value"] == 2500
    assert math.isnan(fn.loc["impressions", "step_rate"])          # no prior stage
    assert fn.loc["clicks", "step_rate"] == pytest.approx(2500 / 150000)     # CTR
    assert fn.loc["sessions", "step_rate"] == pytest.approx(2000 / 2500)     # landing rate
    assert fn.loc["engaged_sessions", "step_rate"] == pytest.approx(900 / 2000)
    assert fn.loc["conversions", "step_rate"] == pytest.approx(120 / 900)


def test_funnel_skips_missing_engagement():
    df = _wk().drop(columns=["sessions", "engaged_sessions"])
    fn = M.funnel(df).set_index("stage")
    assert set(fn.index) == {"impressions", "clicks", "conversions"}
    assert fn.loc["conversions", "step_rate"] == pytest.approx(120 / 2500)   # steps from clicks


def test_format_value_by_type():
    assert M.format_value(0.1234, "pct") == "12.3%"
    assert M.format_value(12.5, "currency") == "$12.50"
    assert M.format_value(5_500_000, "currency") == "$5.50M"
    assert M.format_value(150000, "count") == "150,000"
    assert M.format_value(3.6667, "ratio") == "3.67x"
    assert M.format_value(float("nan"), "pct") == "—"


def test_pyramid_groups_by_tier():
    pyr = M.pyramid(_wk())
    assert set(pyr) == {"reach", "intent", "outcome"}
    assert all(len(pyr[t]) >= 3 for t in pyr)
    rec = pyr["outcome"][0]
    assert {"label", "value", "format", "tier"}.issubset(rec)
