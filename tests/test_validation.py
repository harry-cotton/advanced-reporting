"""Tests for the ground-truth recovery gate (mmm/validation.py).

The last test is the gate itself: fit the baseline engine on the full synthetic DGP and
grade it against the known truth. It is marked xfail(strict=True) because the current
baseline misattributes badly (documented in the 2026-07 architecture review); when the
engine is fixed the test will XPASS and the suite will demand the marker be removed.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from advanced_reporting.mmm.validation import (
    load_ground_truth, recovery_markdown, recovery_report, validate_run)


def _summary(rows):
    return pd.DataFrame(rows, columns=[
        "channel", "spend", "contribution", "contribution_share", "roi",
        "contribution_low", "contribution_high", "roi_low", "roi_high",
        "adstock_decay", "half_sat"])


TRUTH = {
    "a": {"total_spend": 1e6, "total_contribution": 4e6, "roi": 4.0},
    "b": {"total_spend": 1e6, "total_contribution": 2e6, "roi": 2.0},
    "c": {"total_spend": 1e6, "total_contribution": 1e6, "roi": 1.0},
}


def test_perfect_recovery_passes():
    s = _summary([(ch, 1e6, v["total_contribution"], 0.3, v["roi"],
                   v["total_contribution"] * 0.8, v["total_contribution"] * 1.2,
                   v["roi"] * 0.8, v["roi"] * 1.2, 0.4, 5000.0)
                  for ch, v in TRUTH.items()])
    rep = recovery_report(s, TRUTH)
    assert rep["passed"]
    assert rep["rank_corr"] == pytest.approx(1.0)
    assert rep["ci_coverage"] == pytest.approx(1.0)
    assert rep["n_within_tolerance"] == 3
    assert rep["warnings"] == []


def test_inverted_attribution_fails_with_negative_rank_corr():
    # Estimates in reverse order of truth, and 'a' off by 20x.
    s = _summary([
        ("a", 1e6, 0.2e6, 0.05, 0.2, 0.0, 0.5e6, 0.0, 0.5, 0.7, 5000.0),
        ("b", 1e6, 2.0e6, 0.45, 2.0, 1e6, 3e6, 1.0, 3.0, 0.7, 5000.0),
        ("c", 1e6, 4.0e6, 0.50, 4.0, 3e6, 5e6, 3.0, 5.0, 0.7, 5000.0),
    ])
    rep = recovery_report(s, TRUTH)
    assert not rep["passed"]
    assert rep["rank_corr"] < 0
    # every channel selected decay on the grid boundary -> one warning each
    assert sum("search-grid boundary" in w for w in rep["warnings"]) == 3


def test_boundary_warnings_include_ridge_alpha_and_missing_channels():
    s = _summary([("a", 1e6, 4e6, 1.0, 4.0, 3e6, 5e6, 3.0, 5.0, 0.4, 5000.0)])
    rep = recovery_report(s, TRUTH, fit_metrics={"ridge_alpha": 0.5})
    assert any("ridge alpha" in w for w in rep["warnings"])
    assert any("b, c" in w for w in rep["warnings"])  # channels missing from the fit


def test_markdown_carries_verdict_and_figures():
    s = _summary([(ch, 1e6, v["total_contribution"], 0.3, v["roi"],
                   v["total_contribution"] * 0.8, v["total_contribution"] * 1.2,
                   v["roi"] * 0.8, v["roi"] * 1.2, 0.4, 5000.0)
                  for ch, v in TRUTH.items()])
    md = recovery_markdown(recovery_report(s, TRUTH))
    assert "PASS" in md
    for ch in TRUTH:
        assert f"| {ch} |" in md


def test_validate_run_writes_validation_md(tmp_path):
    s = _summary([(ch, 1e6, v["total_contribution"], 0.3, v["roi"],
                   v["total_contribution"] * 0.8, v["total_contribution"] * 1.2,
                   v["roi"] * 0.8, v["roi"] * 1.2, 0.4, 5000.0)
                  for ch, v in TRUTH.items()])
    s.to_csv(tmp_path / "channel_summary.csv", index=False)
    (tmp_path / "ground_truth.json").write_text(
        json.dumps({"weeks": 104, "channels": TRUTH}), encoding="utf-8")
    rep = validate_run(tmp_path)
    assert rep is not None and rep["passed"]
    assert (tmp_path / "validation.md").exists()
    assert load_ground_truth(tmp_path / "ground_truth.json") == TRUTH


def test_validate_run_is_noop_without_answer_key(tmp_path):
    assert validate_run(tmp_path) is None
    assert not (tmp_path / "validation.md").exists()


@pytest.mark.xfail(
    strict=True,
    reason="Known defect (2026-07 review): the baseline engine misattributes on the full "
           "DGP — contaminated hyperparameter selection + decay search saturating at the "
           "grid boundary. When the engine is fixed this will XPASS: remove the marker.")
def test_baseline_recovers_full_dgp_ground_truth():
    """THE gate: fit on the real synthetic DGP, grade against the answer key."""
    from advanced_reporting.ingestion.synthetic import build_kpi_frame, simulate_weekly
    from advanced_reporting.mmm.baseline import BaselineMMM

    weeks, t, spend_wk, contrib_wk, truth = simulate_weekly(np.random.default_rng(42))
    kpi = build_kpi_frame(weeks, t, contrib_wk, np.random.default_rng(1))
    df = pd.DataFrame({"date": weeks, **spend_wk}).merge(kpi, on="date")

    res = BaselineMMM().fit(df, list(spend_wk), ["price_index", "promo_flag"],
                            "revenue", "date")
    rep = recovery_report(res.channel_summary, truth)
    assert rep["passed"], (
        f"recovery failed: rank corr {rep['rank_corr']:.2f}, "
        f"{rep['n_within_tolerance']}/{rep['n_channels']} within tolerance; "
        f"warnings: {rep['warnings']}")
