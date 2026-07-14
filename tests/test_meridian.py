"""Meridian adapter tests. The geo-table builder + the posterior->MMMResult MAPPING are
CI-safe (a mock analyzer with the xarray shapes Meridian 1.7.0 returns — no MCMC). The real
fit is manual/local (scripts/compare_engines.py); CI never runs Meridian MCMC."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from advanced_reporting.transform.clean import build_modeling_table_geo


def test_build_modeling_table_geo_shapes_and_population():
    weekly_geo = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-01-08", "2024-01-08"]),
        "channel": ["meta", "meta", "meta", "google_search"],
        "geo": ["US-A", "US-B", "US-A", "US-A"],
        "spend": [100.0, 200.0, 150.0, 50.0],
    })
    kpi = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-01-08", "2024-01-08"]),
        "geo": ["US-A", "US-B", "US-A", "US-B"],
        "submitted_applications": [10, 20, 12, 22],
    })
    geo = build_modeling_table_geo(weekly_geo, kpi, ["meta", "google_search"],
                                   "submitted_applications", populations={"US-A": 5.0, "US-B": 9.0})
    # one row per (date, geo) that has a KPI; channel columns present; population mapped
    assert {"date", "geo", "meta", "google_search", "submitted_applications", "population"} <= set(geo.columns)
    assert geo.loc[geo["geo"] == "US-A", "population"].iloc[0] == 5.0
    row = geo[(geo["geo"] == "US-A") & (geo["date"] == pd.Timestamp("2024-01-01"))].iloc[0]
    assert row["meta"] == 100.0 and row["google_search"] == 0.0     # missing channel -> 0
    assert not geo[["date", "geo"]].duplicated().any()


# ------------------------------------------------------------------ mapping (mock analyzer)
xr = pytest.importorskip("xarray")


def _mock_analyzer(channels, times):
    metrics = ["mean", "median", "ci_lo", "ci_hi"]
    dist = ["prior", "posterior"]
    nc, nt = len(channels), len(times)

    def _cmd(base):  # (channel, metric, distribution) with a clean posterior-mean pattern
        a = np.zeros((nc, 4, 2))
        for i in range(nc):
            a[i, 0] = base[i]                 # mean
            a[i, 1] = base[i]                 # median
            a[i, 2] = base[i] * 0.6           # ci_lo
            a[i, 3] = base[i] * 1.4           # ci_hi
        return xr.DataArray(a, dims=["channel", "metric", "distribution"],
                            coords={"channel": channels, "metric": metrics, "distribution": dist})

    contrib = [1000.0, 200.0][:nc] + [500.0] * max(0, nc - 2)
    roi = [c / s for c, s in zip(contrib, [1e5, 1e5][:nc] + [1e5] * max(0, nc - 2))]
    sm = xr.Dataset({
        "spend": xr.DataArray([1e5] * nc, dims=["channel"], coords={"channel": channels}),
        "incremental_outcome": _cmd(contrib),
        "roi": _cmd(roi),
        "pct_of_contribution": _cmd([50.0] * nc),
    })

    ev = xr.Dataset({
        "expected": xr.DataArray(np.ones((3, nt)) * 100, dims=["metric", "time"],
                                 coords={"metric": ["mean", "ci_lo", "ci_hi"], "time": times}),
        "baseline": xr.DataArray(np.ones((3, nt)) * 40, dims=["metric", "time"],
                                 coords={"metric": ["mean", "ci_lo", "ci_hi"], "time": times}),
        "actual": xr.DataArray(np.ones(nt) * 105, dims=["time"], coords={"time": times}),
    })
    pa = xr.Dataset({"value": xr.DataArray(
        np.array([[[0.90, 0.85, 0.88], [0.95, 0.80, 0.90]],       # R_Squared
                  [[0.05, 0.07, 0.06], [0.04, 0.08, 0.05]],       # MAPE
                  [[0.05, 0.07, 0.06], [0.04, 0.08, 0.05]]]),     # wMAPE
        dims=["metric", "geo_granularity", "evaluation_set"],
        coords={"metric": ["R_Squared", "MAPE", "wMAPE"],
                "geo_granularity": ["geo", "national"],
                "evaluation_set": ["Train", "Test", "All Data"]})})
    mult = [0.0, 0.5, 1.0, 1.5]
    rc = xr.Dataset({
        "spend": xr.DataArray(np.outer(mult, [1e5] * nc), dims=["spend_multiplier", "channel"],
                              coords={"spend_multiplier": mult, "channel": channels}),
        "incremental_outcome": xr.DataArray(
            np.zeros((len(mult), nc, 3)), dims=["spend_multiplier", "channel", "metric"],
            coords={"spend_multiplier": mult, "channel": channels,
                    "metric": ["mean", "ci_lo", "ci_hi"]}),
    })

    class _A:
        def summary_metrics(self, **k): return sm
        def expected_vs_actual_data(self, **k): return ev
        def predictive_accuracy(self, **k): return pa
        def response_curves(self, **k): return rc
        def incremental_outcome(self, **k):
            return np.ones((2, 8, nt, nc))          # (chains, draws, time, channel)
        def adstock_decay(self): raise RuntimeError("not needed")
    return _A()


def test_meridian_mapping_from_mock_posterior():
    from advanced_reporting.mmm.meridian_engine import MeridianMMM
    channels = ["google_search", "audio"]
    times = [f"2024-01-{d:02d}" for d in range(1, 6)]
    res = MeridianMMM()._to_result(_mock_analyzer(channels, times), channels, times, times, times)

    assert res.engine == "meridian"
    s = res.channel_summary.set_index("channel")
    # posterior mean + flipped CI map straight through
    assert s.loc["google_search", "contribution"] == pytest.approx(1000.0)
    assert s.loc["google_search", "contribution_low"] == pytest.approx(600.0)
    assert s.loc["google_search", "contribution_high"] == pytest.approx(1400.0)
    # held-out R² comes from the Test evaluation_set (0.80), in-sample from Train (0.95)
    assert res.fit_metrics["test_r2"] == pytest.approx(0.80)
    assert res.fit_metrics["r2"] == pytest.approx(0.95)
    # actual/predicted/baseline lengths line up with the weeks
    assert len(res.actual) == len(times) and len(res.predicted) == len(times)
    assert "baseline" in res.contributions.columns
    assert set(res.response_curves) == set(channels)


def test_meridian_requires_geo_df():
    meridian = pytest.importorskip("meridian")   # only where the heavy dep is installed
    from advanced_reporting.mmm.meridian_engine import MeridianMMM
    nat = pd.DataFrame({"date": pd.to_datetime(["2024-01-01"]), "meta": [1.0], "kpi": [1.0]})
    with pytest.raises(ValueError, match="GEO"):
        MeridianMMM().fit(nat, ["meta"], [], "kpi", "date", geo_df=None)
