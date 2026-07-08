"""Global-filter tests — pure pandas apply() (no Streamlit runtime)."""
from __future__ import annotations

import pandas as pd

from advanced_reporting.dashboard import filters


def _df() -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=6, freq="W-MON")
    rows = []
    for d in dates:
        rows.append({"date": d, "channel": "meta", "spend": 100.0})
        rows.append({"date": d, "channel": "linkedin", "spend": 50.0})
    return pd.DataFrame(rows)


def test_apply_channel_filter():
    out = filters.apply(_df(), None, ["meta"])
    assert set(out["channel"]) == {"meta"}


def test_apply_empty_channels_means_all():
    df = _df()
    assert len(filters.apply(df, None, [])) == len(df)      # empty = all
    assert len(filters.apply(df, None, None)) == len(df)


def test_apply_date_range_inclusive():
    df = _df()
    dates = sorted(df["date"].unique())
    out = filters.apply(df, (dates[1], dates[3]), None)
    assert out["date"].min() == dates[1] and out["date"].max() == dates[3]


def test_apply_partial_daterange_is_ignored():
    df = _df()
    # a single-element range (mid-selection) must not filter
    assert len(filters.apply(df, (df["date"].min(),), None)) == len(df)


def test_apply_combined():
    df = _df()
    dates = sorted(df["date"].unique())
    out = filters.apply(df, (dates[0], dates[1]), ["linkedin"])
    assert set(out["channel"]) == {"linkedin"}
    assert out["date"].max() == dates[1]
