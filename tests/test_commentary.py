"""Commentary guardrail tests — the 'uncertainty-aware, never over-claim' invariant is a
documented key decision (CLAUDE.md); these are its first enforcement (2026-07 review)."""
from types import SimpleNamespace

import pandas as pd

from advanced_reporting.reporting.commentary import generate_commentary


def _result(rows, r2=0.85, test_r2=0.75, params=None):
    s = pd.DataFrame(rows, columns=["channel", "spend", "contribution",
                                    "contribution_low", "contribution_high",
                                    "roi", "roi_low", "roi_high"])
    return SimpleNamespace(
        engine="baseline", channel_summary=s, params=params or {},
        fit_metrics=dict(r2=r2, test_r2=test_r2, mape=0.05, test_mape=0.06,
                         n_obs=104, n_train=88, ridge_alpha=1.0))


def test_confidently_unprofitable_channel_is_flagged():
    # ROI interval entirely below 1.0 used to fall through every branch and print
    # "No strong flags" while the channel lost money
    res = _result([("meta", 1e6, 4e5, 3e5, 6e5, 0.4, 0.2, 0.6)])
    md = generate_commentary(res)
    assert "losing money" in md
    assert "No flags" not in md and "No strong flags" not in md


def test_point_below_breakeven_straddling_is_flagged_as_unproven():
    res = _result([("meta", 1e6, 9.5e5, 5e5, 1.4e6, 0.95, 0.5, 1.4)])
    md = generate_commentary(res)
    assert "unproven" in md


def test_headroom_never_advertised_for_unprofitable_channel():
    # below saturation midpoint AND confidently unprofitable -> must not say "scale"
    res = _result([("meta", 1e6, 4e5, 3e5, 6e5, 0.4, 0.2, 0.6)],
                  params={"meta": {"mean_spend": 1000.0, "half_sat": 5000.0}})
    md = generate_commentary(res)
    assert "headroom to scale" not in md
    assert "losing money" in md


def test_fit_adjective_keyed_to_holdout_with_overfit_warning():
    res = _result([("meta", 1e6, 2e6, 1.5e6, 2.5e6, 2.0, 1.6, 2.4)],
                  r2=0.95, test_r2=0.30)
    md = generate_commentary(res)
    assert "**weak** held-out accuracy" in md          # not "strong" from in-sample 0.95
    assert "overfitting" in md


def test_intervals_and_hedged_language_present():
    res = _result([("meta", 1e6, 2e6, 1.5e6, 2.5e6, 2.0, 1.6, 2.4)])
    md = generate_commentary(res)
    assert "90%" in md and "associated with" in md
    assert "not proven causation" in md
