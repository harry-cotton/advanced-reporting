"""Narrative-insight tests — deterministic titles/prose computed from weekly tables.
Pure pandas (no Streamlit runtime), mirroring test_dashboard_wiring.py."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from advanced_reporting.dashboard import insights


def _weekly(measured: bool = True) -> pd.DataFrame:
    """8 weeks, 2 paid channels + organic; outcomes double in the second half."""
    dates = pd.date_range("2026-01-05", periods=8, freq="W-MON")
    rows = []
    for i, d in enumerate(dates):
        mult = 1.0 if i < 4 else 2.0
        ke = (lambda v: v * mult) if measured else (lambda v: np.nan)
        rows.append({"date": d, "channel": "meta", "spend": 1000.0,
                     "conversions": 40.0 * mult, "key_events": ke(20.0)})
        rows.append({"date": d, "channel": "linkedin", "spend": 1000.0,
                     "conversions": 30.0 * mult, "key_events": ke(10.0)})
        rows.append({"date": d, "channel": "organic_search", "spend": 0.0,
                     "conversions": 0.0, "key_events": 5.0 * mult if measured else np.nan})
    return pd.DataFrame(rows)


def test_kpi_trend_measured():
    b = insights.kpi_trend_insight(_weekly(), "start applications")
    assert "up" in b["title"] and b["measured"]
    # recent 4 wks (70/wk) vs prior 4 wks (35/wk): +100%
    assert b["trend_pct"] == pytest.approx(1.0, abs=0.01)
    assert "start applications" in b["narrative"]
    assert "Meta" in b["narrative"]                    # top paid contributor named
    assert set(b["series"].columns) == {"Paid media", insights.NONPAID_LABEL}
    assert b["annotations"][0][2].startswith("peak week")


def test_kpi_trend_degrades_to_claimed_with_honest_label():
    b = insights.kpi_trend_insight(_weekly(measured=False))
    assert not b["measured"]
    assert "platform-claimed" in b["title"].lower()
    assert "directional" in b["narrative"]             # the honesty note is woven in


def test_claims_vs_measured_signature_block():
    b = insights.claims_vs_measured_insight(_weekly())
    # meta 480/240 = 2x, linkedin 360/120 = 3x -> overall 840/360
    assert b["overall_ratio"] == pytest.approx(840 / 360)
    assert "2.3x" in b["title"]
    assert "LinkedIn" in b["narrative"]                # widest gap called out
    per = b["per_channel"].set_index("channel")
    assert per.loc["linkedin", "ratio"] == pytest.approx(3.0)
    # no analytics series -> the signature block does NOT render (never faked)
    assert insights.claims_vs_measured_insight(_weekly(measured=False)) is None


def test_cost_per_outcome_ranking():
    b = insights.cost_per_outcome_insight(_weekly(), "start applications")
    assert b["measured"]
    per = b["per_channel"].set_index("channel")
    assert per.loc["meta", "cost_per"] == pytest.approx(8000 / 240)
    assert per.loc["linkedin", "cost_per"] == pytest.approx(8000 / 120)
    assert b["title"].startswith("Meta")               # cheapest leads the sentence
    assert "2.0x cheaper than LinkedIn" in b["title"]

    unmeasured = insights.cost_per_outcome_insight(_weekly(measured=False))
    assert not unmeasured["measured"]
    assert "platform-claimed" in unmeasured["narrative"]


def test_pacing_without_budget_shows_run_rate():
    b = insights.pacing_insight(_weekly())
    assert b["budget"] is None
    assert "/week" in b["title"]
    assert b["total_spend"] == pytest.approx(16000)
    assert "No budget is configured" in b["narrative"]


@pytest.mark.parametrize("total,expected", [
    (32000, "on plan"),        # 50% spent, 50% elapsed
    (64000, "behind"),         # 25% spent, 50% elapsed
    (20000, "ahead"),          # 80% spent, 50% elapsed
])
def test_pacing_verdicts(total, expected):
    b = insights.pacing_insight(_weekly(), {"total": total, "flight_weeks": 16})
    assert b["budget"]["verdict"].startswith(expected.split()[0].replace("on", "on_"))
    assert expected.split()[0] in b["title"]


def _hist_with_audiences() -> pd.DataFrame:
    """Minimal history stand-in: 3 decoded audience rows per week across 4 weeks
    (two PROSPECT audiences with a 2.5x spread, one lone RETARGET)."""
    dates = pd.date_range("2026-01-05", periods=4, freq="W-MON")
    rows = []
    for d in dates:
        rows.append({"date": d, "channel": "meta",
                     "campaign": "US_META_CONVERT_PROSPECT",
                     "ad_group": "PROSPECT_LAL-1PCT_FEED",
                     "audience_type": "PROSPECT", "audience_detail": "LAL-1PCT",
                     "creative": "", "creative_format": "",
                     "spend": 500.0, "conversions": 25.0})    # $20 / claimed conv
        rows.append({"date": d, "channel": "meta",
                     "campaign": "US_META_CONVERT_PROSPECT",
                     "ad_group": "PROSPECT_INT-GRADS_FEED",
                     "audience_type": "PROSPECT", "audience_detail": "INT-GRADS",
                     "creative": "", "creative_format": "",
                     "spend": 500.0, "conversions": 10.0})    # $50 / claimed conv
        rows.append({"date": d, "channel": "meta",
                     "campaign": "US_META_CONVERT_RETARGET",
                     "ad_group": "RETARGET_SITE-90D_FEED",
                     "audience_type": "RETARGET", "audience_detail": "SITE-90D",
                     "creative": "", "creative_format": "",
                     "spend": 500.0, "conversions": 50.0})    # $10 — cheap, warm, alone
    return pd.DataFrame(rows)


def test_topline_summary_with_measured():
    s = insights.topline_summary(_weekly(), "start applications")
    assert "$16" in s                           # total spend (format: $16.0k or $16,000)
    assert "start applications" in s
    assert "Meta" in s and "LinkedIn" in s      # efficiency gap named
    assert "×" in s                        # claim ratio × present


def test_topline_summary_degrades_to_claimed():
    s = insights.topline_summary(_weekly(measured=False))
    assert "platform-claimed" in s
    assert "×" not in s                         # no claim ratio without measured


def test_audience_callout_compares_within_one_type():
    hist = _hist_with_audiences()
    b = insights.audience_callout_insight(hist)
    assert b is not None
    # RETARGET ($10) is globally cheapest but stands alone — the callout must NOT
    # cross types (warm converts cheaper by construction). Within PROSPECT:
    # LAL-1PCT $20 vs INT-GRADS $50 → 2.5x.
    assert b["best"]["audience_type"] == b["worst"]["audience_type"] == "PROSPECT"
    assert b["mult"] == pytest.approx(2.5, abs=0.01)
    assert "Among PROSPECT audiences" in b["title"]
    assert "platform-claimed" in b["narrative"].lower()
    assert "within one audience type" in b["narrative"].lower()


def test_audience_callout_returns_none_without_same_type_pair():
    # one audience per type → no honest within-type comparison exists
    hist = _hist_with_audiences()
    hist = hist[hist["audience_detail"] != "INT-GRADS"]
    assert insights.audience_callout_insight(hist) is None


def test_audience_callout_returns_none_without_ad_level():
    # campaign-level rows (ad_group == "") → None
    hist = _hist_with_audiences().copy()
    hist["ad_group"] = ""
    assert insights.audience_callout_insight(hist) is None


def test_audience_callout_returns_none_for_unparsed_only():
    hist = _hist_with_audiences().copy()
    hist["audience_type"] = "(unparsed)"
    assert insights.audience_callout_insight(hist) is None


def test_audience_callout_returns_none_without_column():
    # hist without ad_group column at all (pre-v5 store)
    hist = _hist_with_audiences().drop(columns=["ad_group"])
    assert insights.audience_callout_insight(hist) is None


def _weekly_reach() -> pd.DataFrame:
    """4 weeks, 2 paid channels with a clean CPM/CPC spread + organic (no spend)."""
    dates = pd.date_range("2026-01-05", periods=4, freq="W-MON")
    rows = []
    for d in dates:
        # meta: cheap CPM ($5), linkedin: dear CPM ($20)
        rows.append({"date": d, "channel": "meta", "spend": 1000.0,
                     "impressions": 200_000.0, "clicks": 2_000.0})
        rows.append({"date": d, "channel": "linkedin", "spend": 1000.0,
                     "impressions": 50_000.0, "clicks": 500.0})
        rows.append({"date": d, "channel": "organic_search", "spend": 0.0,
                     "impressions": 0.0, "clicks": 0.0})
    return pd.DataFrame(rows)


def test_rag_gauge_absolute_cost_verdicts():
    # cost metric (higher_is_better=False): good <= warn
    assert insights._rag_gauge(1.5, False, good=2.0, warn=4.0)["verdict"] == "good"
    assert insights._rag_gauge(3.0, False, good=2.0, warn=4.0)["verdict"] == "warn"
    assert insights._rag_gauge(5.0, False, good=2.0, warn=4.0)["verdict"] == "bad"
    g = insights._rag_gauge(1.5, False, good=2.0, warn=4.0)
    assert g["mode"] == "absolute"
    # band order left→right is good, warn, bad for a cost metric
    assert [b[2] for b in g["band_stops"]] == ["good", "warn", "bad"]


def test_rag_gauge_absolute_rate_verdicts():
    # rate metric (higher_is_better=True): good >= warn
    assert insights._rag_gauge(0.02, True, good=0.012, warn=0.006)["verdict"] == "good"
    assert insights._rag_gauge(0.008, True, good=0.012, warn=0.006)["verdict"] == "warn"
    assert insights._rag_gauge(0.003, True, good=0.012, warn=0.006)["verdict"] == "bad"


def test_rag_gauge_relative_needs_sample():
    # NaN value → None; too-few sample points in relative mode → None
    assert insights._rag_gauge(float("nan"), False) is None
    assert insights._rag_gauge(3.0, False, sample=[3.0]) is None
    g = insights._rag_gauge(3.0, False, sample=[1.0, 3.0, 5.0])
    assert g is not None and g["mode"] == "relative"


def test_tier_scorecard_reach_relative_bands():
    sc = insights.tier_scorecard(_weekly_reach(), "reach")
    assert sc["label"] == "Awareness"
    keys = {r["key"] for r in sc["rag"]}
    assert {"cpm", "cpc"} <= keys                  # efficiency gauges present
    assert sc["relative_bands"]                    # no targets → channel-spread bands
    assert not sc["pace"]                          # no goals configured → no pacing bars
    grid_labels = {lbl for lbl, _ in sc["grid"]}
    assert "Spend" in grid_labels and "Impressions" in grid_labels


def test_tier_scorecard_reach_with_targets_paces_and_grades():
    targets = {"impressions": {"goal": 2_000_000},
               "cpm": {"good": 2.0, "warn": 4.0}}
    sc = insights.tier_scorecard(_weekly_reach(), "reach", targets=targets)
    pace_keys = {p["key"] for p in sc["pace"]}
    assert "impressions" in pace_keys              # goal set → pacing bar
    impr = next(p for p in sc["pace"] if p["key"] == "impressions")
    # total impressions = (200k+50k)*4 = 1.0M vs 2.0M goal → 50%
    assert impr["pct"] == pytest.approx(0.5, abs=0.01)
    cpm = next(r for r in sc["rag"] if r["key"] == "cpm")
    # blended CPM = 8000 spend / 1.0M impr * 1000 = $8 → above warn(4) → bad
    assert cpm["verdict"] == "bad" and cpm["mode"] == "absolute"


def test_tier_scorecard_skips_unpopulated_zero_metrics():
    # video_views / pages_per_session are all-zero (present but never aggregated) → omitted
    wk = _weekly_reach().copy()
    wk["sessions"] = 1000.0
    wk["engaged_sessions"] = 500.0
    wk["page_views"] = 0.0          # unpopulated → pages_per_session must be skipped
    wk["video_views"] = 0.0         # unpopulated → volume metric must be skipped
    sc = insights.tier_scorecard(wk, "intent")
    rag_keys = {r["key"] for r in sc["rag"]}
    assert "pages_per_session" not in rag_keys      # zero ratio not painted "good"
    assert "engagement_rate" in rag_keys            # real value still shown
    grid_labels = {lbl for lbl, _ in sc["grid"]}
    assert "Video views" not in grid_labels         # zero volume omitted
    assert "Sessions" in grid_labels


def test_macro_context_stays_hidden(tmp_path):
    assert insights.macro_context({}) is None
    assert insights.macro_context(
        {"reporting": {"macro_context": {"enabled": False}}}) is None
    # enabled but no notes file -> still hidden (never generated)
    cfg = {"reporting": {"macro_context": {"enabled": True,
                                           "notes_file": str(tmp_path / "none.md")}}}
    assert insights.macro_context(cfg) is None
    notes = tmp_path / "notes.md"
    notes.write_text("# heading ignored\nMBA search demand rose in Q1 (source: X)\n",
                     encoding="utf-8")
    cfg["reporting"]["macro_context"]["notes_file"] = str(notes)
    assert insights.macro_context(cfg) == ["MBA search demand rose in Q1 (source: X)"]
