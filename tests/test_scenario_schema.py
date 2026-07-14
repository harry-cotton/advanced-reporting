"""Scenario-spec schema checks: the committed FBI scenario must validate, and the
validator must catch the ways a spec can go wrong (shares off, bad flight dates, an
incomplete pipeline). P0 acceptance: `config/scenarios/fbi_recruitment.yaml` validates."""
from __future__ import annotations

import copy

import pytest

from advanced_reporting.ingestion import scenario as sc


@pytest.fixture(scope="module")
def spec():
    # load_scenario raises if the committed spec is invalid — the P0 acceptance gate.
    return sc.load_scenario("fbi_recruitment")


def test_committed_scenario_validates(spec):
    assert sc.validate_scenario(spec) == []
    assert spec["name"] == "fbi_recruitment"
    assert spec["flight"]["weeks"] == 131
    assert spec["meta"]["mmm_target"] == "submitted_applications"
    assert spec["meta"]["target_kind"] == "count"


def test_scenario_headline_shapes(spec):
    # 8 paid channels + 4 non-paid; SA is the ~45% hero.
    paid = [c for c, v in spec["channels"].items() if v["kind"] == "paid"]
    nonpaid = [c for c, v in spec["channels"].items() if v["kind"] == "nonpaid"]
    assert len(paid) == 8 and len(nonpaid) == 4
    assert "tiktok" not in spec["channels"]                 # federal device ban — realism
    sa = next(i for i in spec["initiatives"] if i["code"] == "SA")
    assert sa["spend_share"] == pytest.approx(0.45)
    assert len(spec["geos"]) == 10
    assert len(spec["pipeline"]["stages"]) == 6


def test_shares_sum_to_one(spec):
    paid_shares = [v["spend_share"] for v in spec["channels"].values() if v["kind"] == "paid"]
    assert sum(paid_shares) == pytest.approx(1.0, abs=0.01)
    assert sum(i["spend_share"] for i in spec["initiatives"]) == pytest.approx(1.0, abs=0.01)


def test_validator_flags_bad_paid_shares(spec):
    bad = copy.deepcopy(spec)
    bad["channels"]["google_search"]["spend_share"] = 0.99
    problems = sc.validate_scenario(bad)
    assert any("spend_share sums" in p for p in problems)


def test_validator_flags_non_monday_flight(spec):
    bad = copy.deepcopy(spec)
    bad["flight"]["start"] = "2024-01-02"      # a Tuesday
    problems = sc.validate_scenario(bad)
    assert any("Monday" in p for p in problems)


def test_validator_flags_weeks_mismatch(spec):
    bad = copy.deepcopy(spec)
    bad["flight"]["weeks"] = 130
    problems = sc.validate_scenario(bad)
    assert any("disagrees with dates" in p for p in problems)


def test_validator_flags_incomplete_pipeline(spec):
    bad = copy.deepcopy(spec)
    del bad["pipeline"]["paths"]["SA"]["pass_rate"]["testing"]
    problems = sc.validate_scenario(bad)
    assert any("missing stage testing" in p for p in problems)


def test_validator_flags_missing_top_level():
    problems = sc.validate_scenario({"name": "x"})
    assert any("missing top-level key: channels" in p for p in problems)


def test_load_scenario_raises_on_invalid(tmp_path):
    bad = tmp_path / "broken.yaml"
    bad.write_text("name: broken\nseed: 1\n", encoding="utf-8")
    with pytest.raises(sc.ScenarioError):
        sc.load_scenario(bad)


def test_naming_vocab_matches_initiatives(spec):
    declared = {i["code"] for i in spec["initiatives"]}
    assert set(spec["naming_vocab"]["initiatives"]) == declared
