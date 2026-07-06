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
