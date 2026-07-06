"""Planner tests: the naming-generator round-trip (answer key), rails enforcement,
deterministic allocation, the deterministic plan path, and the guarded LLM path (mocked)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from advanced_reporting.planner import (plan_campaign, load_rails, write_plan_xlsx,
                                        check, enforce, PlannerValidationError)
from advanced_reporting.planner import planner as P
from advanced_reporting.planner import evidence
from advanced_reporting.planner.allocator import allocate
from advanced_reporting.planner.rails import channel_bounds
from advanced_reporting.planner.schema import PLAN_COLS, Platform, PlannerTrace

ROOT = Path(__file__).resolve().parents[1]


# --- fixtures --------------------------------------------------------------------------

@pytest.fixture
def rails():
    return load_rails()


def _goals(goal="awareness", budget=100_000.0):
    return {"goal": goal, "total_budget": budget, "market": "US",
            "campaign": f"{goal}_campaign", "primary_kpi": "conversions"}


def _spec(channels, goal="awareness", objective="AWARENESS"):
    return {"goal": goal, "objective": objective, "channels": list(channels),
            "audiences": [{"audience_type": "PROSPECT", "audience_detail": "BROAD",
                           "placement": "FEED"}],
            "creatives": [{"creative": "BRANDHERO", "format": "VID", "size": "9x16"}]}


def _bare_goals(budget=100_000.0):
    return {"client": "", "market": "US", "campaign": "c", "flight_start": "",
            "flight_end": "", "total_budget": budget, "primary_kpi": "conversions",
            "version": "V1", "goal": "awareness"}


def _plan(channels, rails, budget=100_000.0):
    return P._assemble_plan(_bare_goals(budget), rails, _spec(channels), PlannerTrace())


def _curves(specs):
    """specs: list of (channel, coef, half) -> concave revenue curves."""
    out = {}
    spend = np.linspace(0, 100_000, 80)
    for ch, coef, half in specs:
        out[ch] = {"spend": spend, "response": coef * spend / (spend + half),
                   "mean_spend": 20_000.0}
    return out


def _mmm_result(n=40, seed=0):
    from advanced_reporting.mmm.factory import get_engine
    rng = np.random.default_rng(seed)
    chans = ["meta", "tiktok", "google_search", "google_pmax", "linkedin"]
    df = pd.DataFrame({"date": pd.date_range("2025-01-06", periods=n, freq="W-MON")})
    for ch in chans:
        df[ch] = rng.uniform(1_000, 5_000, n)
    df["price_index"] = rng.uniform(0.9, 1.1, n)
    df["promo_flag"] = rng.integers(0, 2, n).astype(float)
    df["revenue"] = (2.0 * df["meta"] + 1.5 * df["tiktok"] + 3.0 * df["google_search"]
                     + 2.5 * df["google_pmax"] + 1.0 * df["linkedin"]
                     + 5_000 * df["promo_flag"] + rng.normal(0, 2_000, n) + 20_000)
    return get_engine("baseline", n_boot=30).fit(
        df, chans, ["price_index", "promo_flag"], "revenue", "date")


# --- 1. round-trip / answer key --------------------------------------------------------

def test_plan_cols_match_generator():
    from naming import naming_generator
    assert PLAN_COLS == naming_generator.PLAN_COLS


def test_round_trip_into_generator(rails, tmp_path):
    from naming import naming_generator

    plan = plan_campaign(_goals("awareness", 90_000), rails, use_llm=False)
    rows = plan.to_plan_rows()
    assert rows and all(set(r) == set(PLAN_COLS) for r in rows)   # zero-glue contract

    in_xlsx = tmp_path / "plan.xlsx"
    out_xlsx = tmp_path / "traffic.xlsx"
    write_plan_xlsx(plan, in_xlsx)
    cols, records, warnings = naming_generator.generate(str(in_xlsx), str(out_xlsx))

    assert records, "generator produced no rows"
    assert warnings == [], f"generator flagged issues: {warnings}"
    canon = {r["canonical_channel"] for r in records}
    assert {"tiktok", "meta"} & canon


# --- 2. validate: reject + repair ------------------------------------------------------

def test_check_flags_disallowed_channel(rails):
    plan = plan_campaign(_goals("awareness"), rails, use_llm=False)
    plan.stages[0].platforms.append(Platform(channel="ROGUE", budget=0.0))
    issues = check(plan, rails)
    assert any("not in allowed" in i for i in issues)


def test_enforce_repairs_disallowed_channel(rails):
    plan = plan_campaign(_goals("awareness"), rails, use_llm=False)
    plan.stages[0].platforms.append(Platform(channel="ROGUE", budget=12_345.0))
    enforce(plan, rails)
    channels = {pf.channel for st in plan.stages for pf in st.platforms}
    assert "ROGUE" not in channels
    assert check(plan, rails) == []          # repaired to a fully valid plan


def test_enforce_raises_when_unrepairable(rails):
    plan = _plan(["ROGUE"], rails)            # only a disallowed channel -> nothing survives
    with pytest.raises(PlannerValidationError):
        enforce(plan, rails)


def test_check_flags_over_cap_audiences(rails):
    plan = _plan(["meta"], rails)
    from advanced_reporting.planner.schema import Audience
    cap = rails["caps"]["max_audiences_per_stage"]
    plan.stages[0].platforms[0].audiences = [
        Audience(audience_type="PROSPECT", audience_detail=f"A{i}") for i in range(cap + 2)]
    assert any("exceeds cap" in i for i in check(plan, rails))


# --- 3. allocator ----------------------------------------------------------------------

def test_allocator_respects_bounds_and_sums(rails):
    plan = _plan(["meta", "tiktok", "google_search"], rails, budget=100_000)
    curves = _curves([("meta", 6.0, 8_000), ("tiktok", 2.0, 8_000),
                      ("google_search", 1.0, 8_000)])
    allocate(plan, rails, curves=curves)
    lo, hi = channel_bounds(rails, 100_000)
    budgets = {pf.channel: pf.budget for st in plan.stages for pf in st.platforms}
    assert all(lo - 1 <= b <= hi + 1 for b in budgets.values())
    assert sum(budgets.values()) == pytest.approx(100_000, rel=1e-3)
    assert budgets["meta"] >= budgets["tiktok"] >= budgets["google_search"]  # follows marginal


def test_allocator_no_mmm_fallback_low_confidence(rails):
    plan = _plan(["meta", "tiktok", "google_search"], rails, budget=90_000)
    allocate(plan, rails, curves=None, priors=None)
    budgets = [pf.budget for st in plan.stages for pf in st.platforms]
    assert sum(budgets) == pytest.approx(90_000, rel=1e-3)
    confs = [pf.rec.confidence for st in plan.stages for pf in st.platforms]
    assert all(c <= 0.3 for c in confs)       # flagged low-confidence


def test_allocator_leaf_budgets_reconcile(rails):
    plan = _plan(["meta", "tiktok"], rails, budget=50_000)
    allocate(plan, rails, curves=_curves([("meta", 3.0, 9_000), ("tiktok", 2.0, 9_000)]))
    leaf = sum(cr.budget for *_x, cr in plan.iter_creatives())
    assert leaf == pytest.approx(50_000, rel=1e-3)


# --- 4. deterministic plan_campaign ----------------------------------------------------

def test_plan_campaign_deterministic_valid(rails):
    plan = plan_campaign(_goals("awareness", 80_000), rails, use_llm=False)
    assert check(plan, rails) == []
    assert plan.trace.source == "deterministic"
    assert plan.trace.cost_usd == 0.0 and plan.trace.model is None


def test_plan_campaign_with_mmm_is_curve_grounded(rails):
    plan = plan_campaign(_goals("conversion", 120_000), rails,
                         mmm_result=_mmm_result(), use_llm=False)
    assert check(plan, rails) == []
    assert "mmm_response_curves" in plan.trace.notes
    leaf = sum(cr.budget for *_x, cr in plan.iter_creatives())
    assert leaf == pytest.approx(120_000, rel=0.02)


# --- 5. guarded LLM path (mocked) ------------------------------------------------------

def test_llm_path_clips_to_rails_and_traces_cost(rails, monkeypatch):
    canned = {
        "channels": ["tiktok", "meta", "NOTACHANNEL"],
        "audiences": [{"audience_type": "PROSPECT", "audience_detail": "BROAD",
                       "placement": "FEED"},
                      {"audience_type": "HACK", "audience_detail": "INVENTED"}],
        "creatives": [{"creative": "BRANDHERO", "format": "VID", "size": "9x16"}],
        "rationale": "test"}
    info = {"model": "claude-sonnet-5", "input_tokens": 1_200, "output_tokens": 300,
            "cost_usd": 1_200 / 1e6 * 3.0 + 300 / 1e6 * 15.0, "error": None}
    monkeypatch.setattr(P.llm, "call",
                        lambda prompt, *, model, schema, max_tokens: (canned, info))

    plan = plan_campaign(_goals("awareness", 100_000), rails, use_llm=True)

    channels = {pf.channel for st in plan.stages for pf in st.platforms}
    assert channels and channels <= set(rails["platforms"])           # invented channel dropped
    auds = {(au.audience_type, au.audience_detail)
            for st in plan.stages for pf in st.platforms for au in pf.audiences}
    assert ("HACK", "INVENTED") not in auds                            # invented audience dropped
    assert plan.trace.source == "llm"
    assert (plan.trace.input_tokens, plan.trace.output_tokens) == (1_200, 300)
    assert plan.trace.cost_usd == pytest.approx(info["cost_usd"])      # gateway-priced
    assert check(plan, rails) == []                                    # still rails-valid


def test_llm_failure_falls_back_to_deterministic(rails, monkeypatch):
    # the gateway never raises — it returns (None, info) and logs; the planner must
    # fall back to the deterministic proposer
    info = {"model": "claude-sonnet-5", "input_tokens": 0, "output_tokens": 0,
            "cost_usd": 0.0, "error": "APIConnectionError: network down"}
    monkeypatch.setattr(P.llm, "call",
                        lambda prompt, *, model, schema, max_tokens: (None, info))
    plan = plan_campaign(_goals("awareness"), rails, use_llm=True)
    assert plan.trace.source == "deterministic"
    assert check(plan, rails) == []


# --- 6. evidence -----------------------------------------------------------------------

def test_historical_performance_ratios():
    df = pd.DataFrame({"channel": ["meta", "meta", "tiktok"],
                       "spend": [100.0, 100.0, 200.0], "conversions": [10.0, 10.0, 20.0],
                       "platform_revenue": [400.0, 400.0, 600.0], "clicks": [50.0, 50.0, 40.0]})
    ev = evidence.historical_performance(df)
    assert ev.data["meta"]["roas"] == pytest.approx(4.0)
    assert ev.data["meta"]["cpa"] == pytest.approx(10.0)
    assert ev.data["tiktok"]["cvr"] == pytest.approx(0.5)


def test_demo_grounding_is_flagged_not_silent():
    with pytest.raises(NotImplementedError):
        evidence.historical_performance_by_demo()


# --- 7. Step-5 allocator correctness (2026-07 review) ------------------------------------

def _weekly_curves():
    """Curves in WEEKLY spend units (like the real MMM emits): meta saturates hard at
    $800/wk, tiktok is steep and linear, linkedin is nearly flat."""
    s = np.linspace(0, 2_000, 200)
    return {
        "meta": {"spend": s, "response": 5.0 * np.minimum(s, 800.0), "mean_spend": 600.0},
        "tiktok": {"spend": s, "response": 2.0 * s, "mean_spend": 600.0},
        "linkedin": {"spend": s, "response": 0.1 * s, "mean_spend": 600.0},
    }


def test_allocator_evaluates_weekly_curves_at_weekly_spend(rails):
    # total $30k over 10 weeks = $3k/week across meta+tiktok+linkedin. Before the fix,
    # marginals were evaluated at TOTAL budget (way past the curve grid), every channel
    # clamped at its tail slope, and the split degenerated to bang-bang fill-to-cap.
    g = _goals("awareness", 30_000.0)
    g["channels"] = ["meta", "tiktok", "linkedin"]
    g["n_weeks"] = 10
    plan = plan_campaign(g, load_rails(), mmm_result=None, use_llm=False)
    # swap in known curves and re-allocate to isolate the allocator's behavior
    allocate(plan, load_rails(), curves=_weekly_curves(), n_weeks=10.0)
    budgets = {pf.channel: pf.budget for st in plan.stages for pf in st.platforms}
    assert sum(budgets.values()) == pytest.approx(30_000.0, rel=1e-3)
    # tiktok (steep, unsaturated) beats meta (saturates at $800/wk -> ~$8k total),
    # which beats linkedin (near-zero marginal)
    assert budgets["tiktok"] > budgets["meta"] > budgets["linkedin"]
    # THE regression: meta must stop near its saturation point x flight weeks,
    # not be tail-clamp filled to the 50% cap ($15k)
    assert budgets["meta"] == pytest.approx(8_000.0, abs=1_500.0)


def test_partial_curve_coverage_falls_back_to_rules(rails):
    g = _goals("awareness", 30_000.0)
    g["channels"] = ["meta", "tiktok", "google_search"]   # google_search has no curve
    plan = plan_campaign(g, load_rails(), mmm_result=None, use_llm=False)
    curves = {k: v for k, v in _weekly_curves().items() if k != "linkedin"}
    allocate(plan, load_rails(), curves=curves, n_weeks=10.0)
    note = " ".join(pf.rec.rationale for st in plan.stages for pf in st.platforms if pf.rec)
    assert "cover only some" in note                       # rules fallback, honestly labeled


def test_infeasible_budget_fails_loud_and_early(rails):
    # 1 channel x max 50% = $50k < $100k total: used to crash deep in enforce() with
    # a misleading "budgets sum to 50,000, not total 100,000"
    g = _goals("conversion", 100_000.0)
    g["channels"] = ["meta"]
    with pytest.raises(PlannerValidationError, match="max_pct_per_channel"):
        plan_campaign(g, load_rails(), use_llm=False)


def test_goal_typo_raises_instead_of_silent_conversion_plan(rails):
    # 'awarness' used to silently produce a generic CONVERT plan
    with pytest.raises(ValueError, match="valid goals"):
        plan_campaign({"goal": "awarness", "total_budget": 50_000.0}, rails, use_llm=False)


def test_zero_budget_raises_early(rails):
    with pytest.raises(ValueError, match="total_budget"):
        plan_campaign({"goal": "awareness", "total_budget": 0.0}, rails, use_llm=False)


def test_flight_weeks_from_dates_lands_in_allocation_note(rails):
    g = _goals("awareness", 50_000.0)
    g["flight_start"], g["flight_end"] = "2026-08-03", "2026-09-28"   # 8 weeks
    plan = plan_campaign(g, rails, mmm_result=_mmm_result(), use_llm=False)
    note = " ".join(pf.rec.rationale for st in plan.stages for pf in st.platforms if pf.rec)
    assert "flight 8 wk" in note
    assert "ASSUMED" not in note
    # and the default path says the assumption out loud
    plan2 = plan_campaign(_goals("awareness", 50_000.0), rails,
                          mmm_result=_mmm_result(), use_llm=False)
    note2 = " ".join(pf.rec.rationale for st in plan2.stages for pf in st.platforms if pf.rec)
    assert "ASSUMED" in note2


def test_vocabulary_enforced_outside_llm_path(rails):
    # a hand-built plan with an invented audience used to sail through check()
    plan = plan_campaign(_goals("awareness", 90_000.0), rails, use_llm=False)
    for st in plan.stages:
        for pf in st.platforms:
            for au in pf.audiences:
                au.audience_type, au.audience_detail = "HACK", "INVENTED"
    issues = check(plan, rails)
    assert any("audience library" in i for i in issues)
    with pytest.raises(PlannerValidationError):   # enforce drops them -> plan empties -> loud
        enforce(plan, rails)
