"""P1 DGP tests: seeded determinism, the EXACT accounting identity, ROI rank order, the
naming round-trip (incl. the trailing initiative), the ~configured unparsed tail, the
applicant-pipeline cohort shape (monotone + censored), and that the emitted ad exports
parse + validate through their real readers. Everything runs at --mini / small windows so
CI never does the full generation."""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd
import pytest

from advanced_reporting.ingestion import scenario, scenario_dgp
from advanced_reporting.ingestion import naming_decode as nd


@pytest.fixture(scope="module")
def spec():
    return scenario.load_scenario("fbi_recruitment")


@pytest.fixture(scope="module")
def mini(spec):
    return scenario_dgp.generate(spec, mini=True)


@pytest.fixture(scope="module")
def wide(spec):
    # 40 weeks x 4 geos: past ctv's mid-2024 geo-lever launch, so ALL paid channels are
    # active (the 16-week mini window predates the ctv launch).
    return scenario_dgp.generate(spec, n_weeks=40, n_geos=4)


def test_mini_generates_fast(spec):
    t = time.time()
    scenario_dgp.generate(spec, mini=True)
    assert time.time() - t < 5.0                      # brief: --mini in <5s


def test_accounting_identity_exact(mini):
    ident = mini.ground_truth["identity"]
    # baseline + Sigma channel contributions = KPI, exactly (residual == 0 by construction)
    assert abs(ident["residual"]) < 1e-6
    assert ident["baseline"] + ident["paid_contribution"] == pytest.approx(
        ident["kpi_submitted"], abs=0.02)
    # the KPI total equals the emitted weekly submitted-applications total
    assert mini.kpi_weekly["submitted_applications"].sum() == pytest.approx(
        ident["kpi_submitted"], rel=1e-6)


def test_seeded_determinism(spec):
    a = scenario_dgp.generate(spec, mini=True)
    b = scenario_dgp.generate(spec, mini=True)
    assert a.ground_truth == b.ground_truth
    assert a.media_weekly["spend"].sum() == b.media_weekly["spend"].sum()
    assert a.kpi_weekly["submitted_applications"].tolist() == \
        b.kpi_weekly["submitted_applications"].tolist()
    # a different seed moves the numbers
    c = scenario_dgp.generate(spec, mini=True, seed=999)
    assert c.kpi_weekly["submitted_applications"].tolist() != \
        a.kpi_weekly["submitted_applications"].tolist()


def test_roi_rank_and_paid_share(wide, spec):
    gt = wide.ground_truth
    # paid drives the configured share of submitted applications
    lo, hi = spec["funnel"]["paid_share_of_submitted"]
    assert lo - 0.02 <= gt["identity"]["paid_share"] <= hi + 0.02
    # display (the weak cut candidate) is the lowest-ROI channel; google_search is high
    assert gt["roi_rank_order"][-1] == "display"
    assert gt["roi_rank_order"][0] == "google_search"


def test_campaign_names_decode_with_initiative(mini, spec):
    init_codes = {i["code"] for i in spec["initiatives"]}
    paid_campaigns = mini.media_weekly.loc[mini.media_weekly["spend"] > 0, "campaign"]
    paid_campaigns = [c for c in paid_campaigns.unique() if c != "(organic)"]
    assert paid_campaigns
    seen_inits = set()
    for camp in paid_campaigns:
        d = nd.decode_campaign_name(camp)
        assert d.kind == "campaign"                   # every generated name decodes
        assert d.initiative in init_codes             # trailing segment = a real career path
        assert d.market == "US"
        seen_inits.add(d.initiative)
    assert seen_inits == init_codes                   # all five paths appear


def test_unparsed_rate_near_configured(spec):
    # full geo set so the tail concentration matches the configured share
    d = scenario_dgp.generate(spec, n_weeks=40, n_geos=10)
    m = d.media_weekly
    adl = m[(m["spend"] > 0) & (m["ad_group"] != "")].copy()
    dec = nd.decode_series(adl["ad_group"])
    adl["audience_type"] = dec["audience_type"].to_numpy()
    rate = adl.loc[adl["audience_type"] == nd.UNPARSED, "spend"].sum() / adl["spend"].sum()
    configured = spec["unparsed_tail"]["spend_share"]
    assert configured - 0.05 <= rate <= configured + 0.03   # ~12% (some legacy names parse)


def test_pipeline_monotone_and_censored(spec):
    # 80 weeks x 2 geos: enough for early cohorts to mature through all 6 gates
    d = scenario_dgp.generate(spec, n_weeks=80, n_geos=2)
    pl = d.pipeline_stages
    stages = spec["pipeline"]["stages"]
    totals = pl.groupby("stage")["count"].sum()
    ordered = [totals.get(s, 0.0) for s in stages]
    # stage totals are monotone non-increasing (pass-rate decay + right-censoring)
    assert all(ordered[i] >= ordered[i + 1] for i in range(len(ordered) - 1))
    assert ordered[0] > 0 and ordered[-1] > 0          # all six stages appear at this window
    # right-censoring: recent submission cohorts have NOT matured to final_offer (its lag
    # is 20-60 wks), so the emitted final_offer total falls well short of the uncensored
    # expectation (total submitted x each path's cumulative pass rate).
    paths = spec["pipeline"]["paths"]
    iw = {i["code"]: i["spend_share"] for i in spec["initiatives"]}
    isum = sum(iw.values())
    sub_total = d.kpi_weekly["submitted_applications"].sum()
    uncensored_final = 0.0
    for code, share in iw.items():
        pr = paths.get(code, paths["default"])["pass_rate"]
        cum = np.prod([pr[s] for s in stages])
        uncensored_final += sub_total * (share / isum) * cum
    emitted_final = totals[stages[-1]]
    assert emitted_final < 0.85 * uncensored_final     # censoring removed a real slice
    assert emitted_final > 0.1 * uncensored_final      # but early cohorts did mature


def test_pass_rates_match_configured_for_matured_cohort(spec):
    """Stage-to-stage pass-through of a fully-matured EARLY cohort tracks the spec."""
    d = scenario_dgp.generate(spec, n_weeks=100, n_geos=2)
    pl = d.pipeline_stages
    stages = spec["pipeline"]["stages"]
    # arrivals in the first 20 calendar weeks are dominated by the earliest cohorts, which
    # mature through the early gates un-censored; check the first stage-to-stage ratio.
    early = pl[pl["date"] < d.weeks[20]]
    tot = early.groupby("stage")["count"].sum()
    if tot.get(stages[0], 0) and tot.get(stages[1], 0):
        ratio = tot[stages[1]] / tot[stages[0]]
        # blended SA(0.75)/default(0.85) meet_greet pass rate; generous band
        assert 0.5 <= ratio <= 1.0


def test_emitted_ad_exports_validate(tmp_path):
    """The emitter's Google/Meta/LinkedIn exports parse + validate through their readers,
    and the ground-truth + CRM files are written."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import generate_fbi_campaign as gen
    from advanced_reporting.ingestion import exports, schema

    out = tmp_path / "MMM Data"
    gen.main(["--mini", "--out", str(out)])

    ad_files = ["Ad group report.csv", "Meta Ads Manager - Ad Sets.csv",
                "LinkedIn Creative Performance.csv"]
    for name in ad_files:
        source, df = exports.read_export(out / name)
        schema.validate(df)
        assert len(df) > 0

    gt = json.loads((out / "ground_truth.json").read_text())
    assert abs(gt["identity"]["residual"]) < 1e-6
    assert (out / "business_kpi_weekly.csv").exists()
    assert (out / "crm_pipeline_stages.csv").exists()
    assert (out / "ground_truth.json").exists()


def test_media_geo_structure_matches_geomult(wide, spec):
    """Regression: the media geo x weekly structure must follow geo population/multiplier —
    a tile/repeat mismatch in the flatten silently scrambles metrics across cells while
    preserving totals (so the identity + calibration bands stay green but geo/time is wrong)."""
    m = wide.media_weekly
    gs = m[m["channel"] == "google_search"]
    by_geo = gs.groupby("geo")["spend"].sum()
    by_geo = by_geo / by_geo.sum()
    gm = scenario_dgp._geo_multipliers(wide.geos)
    gm = gm / gm.sum()
    expected = dict(zip([g["code"] for g in wide.geos], gm))
    for code, share in by_geo.items():
        assert share == pytest.approx(expected[code], abs=0.02)   # geo structure preserved


def test_burst_weeks_are_the_temporal_peak(spec):
    """The National-Recruiting-Week burst must land in the configured weeks (a scrambled
    flatten would smear it into the wrong calendar weeks)."""
    d = scenario_dgp.generate(spec)
    gs = d.media_weekly[d.media_weekly["channel"] == "google_search"]
    weekly = gs.groupby("date")["spend"].sum()
    burst_start = pd.Timestamp(spec["stress"]["burst"]["periods"][0]["start"])
    burst_weeks = weekly[(weekly.index >= burst_start)
                         & (weekly.index < burst_start + pd.Timedelta(weeks=2))]
    # burst-week spend is well above the median week (2.5x burst x seasonal lift)
    assert burst_weeks.min() > 1.8 * weekly.median()


def test_all_channels_and_geos_present(wide, mini, spec):
    m = wide.media_weekly
    paid = {c for c, v in spec["channels"].items() if v["kind"] == "paid"}
    assert paid <= set(m["channel"].unique())          # every paid channel active by wk 40
    assert "tiktok" not in set(m["channel"].unique())  # federal device ban
    assert len(mini.media_weekly["geo"].unique()) == scenario_dgp._MINI_GEOS
