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


# --- descriptive (no-MMM) commentary: the file-drop phase-3 path -------------------------

def _weekly_mba():
    return pd.DataFrame({
        "channel": ["meta", "google_search", "organic_search", "direct"],
        "spend": [29_000.0, 27_000.0, 0.0, 0.0],
        "impressions": [1e6, 2e5, 0.0, 0.0],
        "clicks": [14_000.0, 5_400.0, 0.0, 0.0],
        "conversions": [776.0, 303.0, 0.0, 0.0],
        "platform_revenue": [0.0, 0.0, 0.0, 0.0],
        "key_events": [388.0, 252.0, 412.0, 241.0],
    })


def test_descriptive_commentary_reports_claims_vs_measured():
    from advanced_reporting.reporting.commentary import generate_descriptive_commentary
    md = generate_descriptive_commentary(_weekly_mba())
    assert "descriptive" in md.lower()
    assert "No causal claims" in md
    # the claims-vs-measured story: platforms claim 1,079, analytics measures 640
    assert "1,079 conversions" in md and "640 key events" in md
    # organic/direct shown as baseline context, not as paid channels
    assert "organic_search" in md and "$0" not in md.split("Non-paid")[1][:200]
    # markdown table well-formed: separator has the same cell count as the header
    lines = md.splitlines()
    hdr = next(line for line in lines if line.startswith("| Channel"))
    sep = lines[lines.index(hdr) + 1]
    assert hdr.count("|") == sep.count("|")
    # never over-claims: no MMM-results language (mentioning the future MMM is fine)
    assert "Est. contribution (90% CI)" not in md
    assert "associated with an estimated" not in md


def test_descriptive_commentary_without_key_events_degrades():
    from advanced_reporting.reporting.commentary import generate_descriptive_commentary
    wk = _weekly_mba().drop(columns=["key_events"])
    md = generate_descriptive_commentary(wk)
    assert "Platform claims vs analytics" not in md      # nothing measured to compare
    assert "| Channel | Spend |" in md
