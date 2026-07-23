"""Intake proposal layer: deterministic facts/proposal; LLM narration is optional
and display-only (no key -> full function, plainer words)."""
from __future__ import annotations

import pandas as pd

from advanced_reporting import llm
from advanced_reporting.agent import intake


def _weekly(measured: bool = True) -> pd.DataFrame:
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-05", "2026-01-12"]),
        "channel": ["meta", "meta"],
        "spend": [5000.0, 5200.0],
        "impressions": [500000.0, 510000.0],
        "clicks": [6000.0, 6100.0],
        "conversions": [400.0, 410.0],
        "sessions": [9000.0, float("nan")],      # partial coverage
        "key_events": [100.0, 105.0],
    })
    if not measured:
        df["key_events"] = float("nan")
    return df


def test_proposal_prefers_measured_over_claimed():
    p = intake.propose_framing(_weekly(measured=True))
    assert p["kpi_metric"] == "key_events"
    assert p["kpi_label"] == "key events"             # registry label, lowercased
    p2 = intake.propose_framing(_weekly(measured=False))
    assert p2["kpi_metric"] == "conversions"


def test_funnel_candidates_are_backed_columns_in_tier_order():
    steps = intake.funnel_candidates(_weekly())
    assert steps == ["impressions", "clicks", "sessions", "conversions",
                     "key_events"]                     # engaged/pages/video absent
    assert "sessions" not in intake.funnel_candidates(_weekly().assign(
        sessions=float("nan")))                        # all-NaN -> not offered


def test_outcome_coverage_counts_weeks():
    cov = intake.outcome_coverage(_weekly())
    assert cov["key_events"] == {"present": True, "kind": "measured",
                                 "weeks_with_data": 2, "weeks_total": 2}
    assert cov["conversions"]["kind"] == "platform-claimed"


def test_proposal_sentence_names_metric_and_coverage():
    wk = _weekly()
    cov = intake.outcome_coverage(wk)
    s = intake.proposal_sentence(intake.propose_framing(wk), cov)
    assert "`key_events`" in s and "2/2 weeks" in s and "measured" in s


def test_narration_is_none_without_a_key(monkeypatch):
    monkeypatch.setattr(llm, "llm_enabled", lambda: False)
    assert intake.narrate_proposal({}, {}) == (None, None)


def test_narration_mocked_call_is_display_only(monkeypatch):
    monkeypatch.setattr(llm, "llm_enabled", lambda: True)
    monkeypatch.setattr(llm, "call", lambda *a, **k: (
        {"narrative": "Because it is measured.",
         "kpi_label_suggestion": "Applications"}, {"cost_usd": 0.0}))
    narrative, suggestion = intake.narrate_proposal(
        {"kpi_metric": "key_events"}, {})
    assert narrative == "Because it is measured."
    assert suggestion == "Applications"

    monkeypatch.setattr(llm, "call", lambda *a, **k: (None, {"error": "boom"}))
    assert intake.narrate_proposal({}, {}) == (None, None)
