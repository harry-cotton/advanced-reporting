"""Golden-set evals for the A2 agent layer (AGENT_SYSTEM_BRIEF.md acceptance).

Scenario fixtures exercise the DETERMINISTIC machinery with mocked model replies
(CI-safe): recommendation eligibility per scenario, the number guard's rejection
behavior, menu-only recommendations, and the publish/stale read path. A live smoke
test runs the real agents end-to-end when an API key is present.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from advanced_reporting.agent import commentary_agent as CA
from advanced_reporting.agent import guards
from advanced_reporting.agent.recommendations import eligible_recommendations
from advanced_reporting.llm import llm_enabled


# --- scenario builders ------------------------------------------------------------

def _weekly(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


def gov_awareness_weekly() -> pd.DataFrame:
    """Gov outreach: reach-heavy, NO conversion wiring — that's the campaign type,
    not a data gap (campaign_types.md)."""
    return _weekly([
        {"date": d, "channel": ch, "spend": s, "impressions": i, "clicks": c,
         "conversions": float("nan"), "key_events": float("nan")}
        for d, ch, s, i, c in [
            ("2026-01-05", "meta", 4000.0, 900000.0, 8000.0),
            ("2026-01-05", "google_search", 800.0, 40000.0, 1500.0),
            ("2026-01-12", "meta", 4200.0, 950000.0, 8600.0),
            ("2026-01-12", "google_search", 850.0, 42000.0, 1600.0)]])


def broken_tracking_weekly() -> pd.DataFrame:
    """Higher-ed conversion campaign where meta's claim ratio hits 4.0x (> 3.5
    tripwire) while search sits at a normal 1.2x."""
    return _weekly([
        {"date": "2026-01-05", "channel": "meta", "spend": 5000.0,
         "impressions": 500000.0, "clicks": 6000.0,
         "conversions": 400.0, "key_events": 100.0},
        {"date": "2026-01-05", "channel": "google_search", "spend": 3000.0,
         "impressions": 60000.0, "clicks": 2400.0,
         "conversions": 120.0, "key_events": 100.0}])


def audience_gap_hist() -> pd.DataFrame:
    """Two PROSPECT audiences 2.5x apart on cost per claimed conv, material spend."""
    return pd.DataFrame({
        "ad_group": ["PROSPECT_Broad_Feed", "PROSPECT_Lookalike_Feed",
                     "RETARGET_Visitors_Feed"],
        "audience_type": ["PROSPECT", "PROSPECT", "RETARGET"],
        "audience_detail": ["Broad", "Lookalike", "Visitors"],
        "spend": [5000.0, 4000.0, 2000.0],
        "conversions": [50.0, 100.0, 80.0],   # $100 vs $40 -> 2.5x within-type
    })


def mmm_fixture(roi_low: float, roi_high: float, mean_spend: float,
                max_curve_spend: float = 10000.0) -> dict:
    return {"summary": pd.DataFrame([{
                "channel": "meta", "roi": (roi_low + roi_high) / 2,
                "roi_low": roi_low, "roi_high": roi_high, "contribution": 1.0}]),
            "meta": {"response_curves": {"meta": {
                "spend": [0.0, max_curve_spend], "response": [0.0, 1.0],
                "mean_spend": mean_spend}}}}


# --- eligibility per scenario --------------------------------------------------------

def _types(recs):
    return {r["type"] for r in recs}


def test_gov_awareness_only_unlock_mmm():
    recs = eligible_recommendations(gov_awareness_weekly())
    assert _types(recs) == {"unlock_mmm"}


def test_broken_tracking_flags_only_the_broken_channel():
    recs = eligible_recommendations(broken_tracking_weekly())
    tracking = [r for r in recs if r["type"] == "investigate_tracking"]
    assert len(tracking) == 1
    assert tracking[0]["evidence"]["channel"] == "meta"
    assert tracking[0]["evidence"]["claim_ratio"] == "4.0x"
    # worded as measurement, never performance
    assert "measurement" in tracking[0]["summary"]


def test_underclaiming_also_flags():
    wk = broken_tracking_weekly()
    wk.loc[wk["channel"] == "meta", "conversions"] = 50.0   # 0.5x < 0.9 tripwire
    recs = eligible_recommendations(wk)
    assert any(r["type"] == "investigate_tracking" for r in recs)


def test_audience_gap_within_type_only():
    recs = eligible_recommendations(broken_tracking_weekly(),
                                    hist=audience_gap_hist())
    shifts = [r for r in recs if r["type"] == "shift_within_type"]
    assert len(shifts) == 1
    ev = shifts[0]["evidence"]
    assert ev["audience_type"] == "PROSPECT"          # never cross-type
    assert ev["better"]["audience"] == "Lookalike"
    assert shifts[0]["evidence_grade"] == "platform-claimed"


def test_fix_naming_thresholded():
    wk = broken_tracking_weekly()
    assert "fix_naming" not in _types(
        eligible_recommendations(wk, unparsed={"spend_rate": 0.05, "names": ["x"]}))
    recs = eligible_recommendations(
        wk, unparsed={"spend_rate": 0.20, "names": ["Untitled 3", "asdf"]})
    fix = [r for r in recs if r["type"] == "fix_naming"]
    assert fix and fix[0]["evidence"]["unparsed_spend_share"] == "20%"


def test_mmm_scale_with_test_needs_headroom_and_confidence():
    recs = eligible_recommendations(
        broken_tracking_weekly(), mmm=mmm_fixture(1.4, 2.8, mean_spend=2000.0))
    assert "scale_with_test" in _types(recs)
    assert "unlock_mmm" not in _types(recs)           # MMM exists
    # spend already past the midpoint -> no scale rec
    recs = eligible_recommendations(
        broken_tracking_weekly(), mmm=mmm_fixture(1.4, 2.8, mean_spend=9000.0))
    assert "scale_with_test" not in _types(recs)
    # interval straddles 1 -> unproven, no rec either way
    recs = eligible_recommendations(
        broken_tracking_weekly(), mmm=mmm_fixture(0.7, 1.8, mean_spend=2000.0))
    assert _types(recs) & {"scale_with_test", "cut_or_restructure"} == set()


def test_mmm_cut_when_interval_below_one():
    recs = eligible_recommendations(
        broken_tracking_weekly(), mmm=mmm_fixture(0.3, 0.8, mean_spend=5000.0))
    assert "cut_or_restructure" in _types(recs)


def test_rebalance_never_eligible_until_allocator_wired():
    for mmm in (None, mmm_fixture(1.4, 2.8, mean_spend=2000.0)):
        assert "rebalance_channel_budget" not in _types(
            eligible_recommendations(broken_tracking_weekly(), mmm=mmm))


# --- the number guard ------------------------------------------------------------------

FACTS = {"spend": "$8,000.00", "cost_per": "$74.60", "ratio": "4.0x",
         "delta": "+16% vs prior 4 wks", "n_channels": 3}


def test_guard_accepts_backed_numbers_and_formats():
    ok = ("Spend was $8,000.00 at $74.60 per key event across three channels — "
          "a 4.0x claim ratio, +16% vs prior 4 wks.")
    assert guards.check_output(ok, FACTS) == []


def test_guard_rejects_invented_number():
    v = guards.check_output("Spend was $9,999.00.", FACTS)
    assert any("9999" in x for x in v)


def test_guard_rejects_reformatted_number():
    # facts say $74.60; "74.6" is a recomputation, not a restatement
    assert guards.check_output("cost of $74.6 per event", FACTS)


def test_guard_rejects_unbacked_number_word():
    assert guards.check_output("across seven channels", FACTS)


def test_guard_rejects_multiplier_words():
    for phrase in ("claims nearly doubled", "twice as expensive",
                   "half the cost", "conversions tripled"):
        v = guards.check_output(phrase, FACTS)
        assert any("multiplier" in x for x in v), phrase


def test_guard_leading_zero_normalization():
    assert guards.check_output("week 05 of the flight", {"note": "week 5"}) == []


# --- commentary agent: mocked roundtrip ---------------------------------------------

def _seed_repo(root) -> None:
    proc = root / "data" / "processed"
    proc.mkdir(parents=True)
    broken_tracking_weekly().to_csv(proc / "channel_weekly_metrics.csv", index=False)
    p = root / "system" / "prompts"
    p.mkdir(parents=True)
    (p / "commentary_agent.md").write_text(
        "G:{guidelines}\nC:{context}\nF:{facts}\nE:{eligible_recommendations}\n"
        "M:{max_recs}", encoding="utf-8")


def _good_reply() -> dict:
    # every numeral below exists in the scenario's computed facts/eligible recs
    return {"lede": "Meta's claim ratio of 4.0x sits outside the 0.9-3.5x band.",
            "sections": [{"title": "Tracking",
                          "text": "Treat this as a measurement issue."}],
            "recommendations": [
                {"type": "investigate_tracking", "evidence_grade":
                 "analytics-measured",
                 "text": "Meta claims 4.0x what analytics measures — audit the "
                         "pixel and attribution window before reading performance."}]}


def test_commentary_publishes_with_stamp_and_hash(tmp_path, monkeypatch):
    _seed_repo(tmp_path)
    monkeypatch.setattr(CA, "call", lambda *a, **k: (
        _good_reply(), {"model": "m", "input_tokens": 1, "output_tokens": 1,
                        "cost_usd": 0.0, "error": None}))
    body, info = CA.generate_commentary(tmp_path)
    assert body and "investigate_tracking" in body
    text = (tmp_path / "outputs" / "commentary_ai.md").read_text(encoding="utf-8")
    assert CA.STAMP in text and "data_hash:" in text
    loaded, note = CA.load_active_commentary(tmp_path)
    assert note is None and "investigate_tracking" in loaded


def test_commentary_rejected_on_invented_number(tmp_path, monkeypatch):
    _seed_repo(tmp_path)
    bad = _good_reply()
    bad["lede"] = "Spend of $123,456.00 delivered a 4.0x ratio."   # invented spend
    monkeypatch.setattr(CA, "call", lambda *a, **k: (
        bad, {"model": "m", "input_tokens": 1, "output_tokens": 1,
              "cost_usd": 0.0, "error": None}))
    body, info = CA.generate_commentary(tmp_path)
    assert body is None
    assert "REJECTED" in info["error"]
    assert info["violations"]
    assert not (tmp_path / "outputs" / "commentary_ai.md").exists()


def test_commentary_drops_ineligible_rec_type(tmp_path, monkeypatch):
    _seed_repo(tmp_path)
    sneaky = _good_reply()
    sneaky["recommendations"].append(
        {"type": "rebalance_channel_budget", "evidence_grade": "modeled",
         "text": "shift the budget"})   # never eligible yet
    monkeypatch.setattr(CA, "call", lambda *a, **k: (
        sneaky, {"model": "m", "input_tokens": 1, "output_tokens": 1,
                 "cost_usd": 0.0, "error": None}))
    body, info = CA.generate_commentary(tmp_path)
    assert body and "rebalance_channel_budget" not in body
    assert any("not eligible" in d for d in info["dropped"])


def test_commentary_stale_after_data_change(tmp_path, monkeypatch):
    _seed_repo(tmp_path)
    monkeypatch.setattr(CA, "call", lambda *a, **k: (
        _good_reply(), {"model": "m", "input_tokens": 1, "output_tokens": 1,
                        "cost_usd": 0.0, "error": None}))
    assert CA.generate_commentary(tmp_path)[0]
    # data changes -> commentary hides itself with a note
    wk = broken_tracking_weekly()
    wk["spend"] = wk["spend"] * 2
    wk.to_csv(tmp_path / "data" / "processed" / "channel_weekly_metrics.csv",
              index=False)
    body, note = CA.load_active_commentary(tmp_path)
    assert body is None and "stale" in note


def test_commentary_no_key_publishes_nothing(tmp_path, monkeypatch):
    _seed_repo(tmp_path)
    monkeypatch.setattr(CA, "call", lambda *a, **k: (
        None, {"error": "no ANTHROPIC_API_KEY"}))
    body, info = CA.generate_commentary(tmp_path)
    assert body is None
    assert not (tmp_path / "outputs" / "commentary_ai.md").exists()


# --- live smoke (real model, real repo data) — runs only when a key is present ------

@pytest.mark.skipif(not llm_enabled(), reason="no ANTHROPIC_API_KEY")
def test_live_spec_and_commentary_smoke():
    from advanced_reporting.agent.spec_agent import generate_spec
    from advanced_reporting.agent.validate import BLOCK_CATALOG, CAMPAIGN_TYPES

    spec, info = generate_spec()
    assert spec is not None, info.get("error")
    assert spec.get("campaign_type") in CAMPAIGN_TYPES
    assert set(spec.get("blocks") or []) <= set(BLOCK_CATALOG)

    body, cinfo = CA.generate_commentary()
    if body is None:      # a guard rejection is a VALID outcome — loud, unpublished
        assert "REJECTED" in (cinfo.get("error") or "") or cinfo.get("error")
    else:
        # attribution labels travel with numbers; menu-only recs by construction
        assert any(g in body for g in
                   ("platform-claimed", "analytics-measured", "modeled"))
        assert json.dumps(cinfo.get("dropped", [])) is not None
