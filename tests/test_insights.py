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


# --- partial (clipped) edge weeks: a flight that starts/ends mid-week -------------------
def _weekly_clipped_edges() -> pd.DataFrame:
    """12 weeks, flat interior (200 outcome / $2k), first & last clipped to ~45% — the
    shape that made the live "-12% MoM" that was really ~flat."""
    dates = pd.date_range("2026-01-05", periods=12, freq="W-MON")
    rows = []
    for i, d in enumerate(dates):
        edge = i in (0, len(dates) - 1)
        ke, sp = (90.0, 900.0) if edge else (200.0, 2000.0)
        rows.append({"date": d, "channel": "meta", "spend": sp,
                     "conversions": ke * 2, "key_events": ke})
    return pd.DataFrame(rows)


def test_partial_edge_weeks_flags_first_and_last():
    ser = _weekly_clipped_edges().groupby("date")["key_events"].sum().sort_index()
    partial = insights._partial_edge_weeks(ser)
    assert list(partial) == [ser.index[0], ser.index[-1]]
    # a series with no clipped edges flags nothing
    flat = pd.Series([200.0] * 8, index=pd.date_range("2026-01-05", periods=8, freq="W-MON"))
    assert len(insights._partial_edge_weeks(flat)) == 0


def test_recent_vs_prior_excludes_partial_weeks():
    ser = _weekly_clipped_edges().groupby("date")["key_events"].sum().sort_index()
    partial = insights._partial_edge_weeks(ser)
    incl = insights._recent_vs_prior(ser)                    # clipped tail included
    excl = insights._recent_vs_prior(ser, exclude=partial)   # clipped edges removed
    assert incl < -0.10                                      # the false dip
    assert abs(excl) < 0.02                                  # honest: interior is flat


def test_kpi_trend_excludes_partial_weeks_and_says_so():
    b = insights.kpi_trend_insight(_weekly_clipped_edges(), "application starts")
    assert len(b["partial_weeks"]) == 2
    assert "part-week" in b["narrative"]                     # transparency note woven in
    assert abs(b["trend_pct"]) < 0.03                        # ~flat, not a fabricated drop
    # the peak annotation ignores the clipped weeks (200, never the 90 edge)
    assert "200" in b["annotations"][0][2]


def test_headline_tiles_delta_flat_when_only_partial_edges_move():
    tiles = {t["label"]: t for t in
             insights.headline_tiles(_weekly_clipped_edges(), "application starts")}
    assert tiles["Application starts"]["delta"] == "flat vs prior 4 wks"


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


# ---------------------------------------------------------------- recruiting pipeline
def _stages() -> pd.DataFrame:
    """Two weeks x two channels of CRM stage counts, funnel-shaped."""
    rows = []
    for d in pd.date_range("2026-01-05", periods=2, freq="W-MON"):
        for ch in ("google_search", "organic_search"):
            for stage, n in (("initial_screening", 100.0), ("meet_greet", 70.0),
                             ("testing", 30.0), ("conditional_offer", 24.0),
                             ("background_investigation", 18.0), ("final_offer", 16.0)):
                rows.append({"date": d, "geo": "US-NE", "initiative": "SA",
                             "stage": stage, "channel": ch, "count": n})
    return pd.DataFrame(rows)


def test_recruiting_pipeline_insight_funnel_and_honesty():
    b = insights.recruiting_pipeline_insight(_stages())
    df = b["stages"]
    assert list(df["stage"]) == insights.PIPELINE_STAGE_ORDER   # canonical order
    # step rates: totals are 2x the per-cell numbers, so rates match the shape
    mg = df.set_index("stage")
    assert mg.loc["meet_greet", "step_rate"] == pytest.approx(0.70)
    assert mg.loc["testing", "step_rate"] == pytest.approx(30 / 70)
    # overall survival to final offer = 16%
    assert b["overall_rate"] == pytest.approx(0.16)
    assert "16%" in b["title"]
    # the hardest gate is Testing (43%), named in the narrative
    assert "Testing" in b["narrative"]
    # the honesty voice + censoring annotation are non-negotiable
    assert "cannot pass a polygraph" in b["narrative"]
    assert "9–12 months" in b["narrative"]
    assert b["censor_note"] == insights.PIPELINE_CENSOR_NOTE


def test_recruiting_pipeline_insight_degrades_to_none():
    assert insights.recruiting_pipeline_insight(None) is None
    assert insights.recruiting_pipeline_insight(pd.DataFrame()) is None
    # a single populated stage cannot make a funnel
    one = _stages()
    one = one[one["stage"] == "initial_screening"]
    assert insights.recruiting_pipeline_insight(one) is None


def test_merge_pipeline_stages_attaches_offer_volumes():
    from advanced_reporting.transform.clean import merge_pipeline_stages
    weekly = _weekly()
    stages = _stages().replace({"channel": {"google_search": "meta"}})
    out = merge_pipeline_stages(weekly, stages)
    assert {"conditional_offers", "final_offers"} <= set(out.columns)
    # national totals survive the join exactly: 2 wks x 2 chs x 24 / x 16
    assert float(out["conditional_offers"].sum()) == pytest.approx(96.0)
    assert float(out["final_offers"].sum()) == pytest.approx(64.0)
    # untouched when there is nothing to merge
    assert merge_pipeline_stages(weekly, None) is weekly


def test_load_pipeline_stages_reads_and_standardizes(tmp_path):
    from advanced_reporting.utils import load_pipeline_stages
    f = tmp_path / "stages.csv"
    _stages().replace({"channel": {"google_search": "organic"}}).to_csv(f, index=False)
    cfg = {"data": {"pipeline_stages_path": str(f)}}
    df = load_pipeline_stages(cfg, tmp_path)
    assert df is not None and set(df["channel"]) == {"organic_search"}  # aliased
    # unset key / missing file -> None (block simply doesn't render)
    assert load_pipeline_stages({"data": {}}, tmp_path) is None
    assert load_pipeline_stages(
        {"data": {"pipeline_stages_path": "nope.csv"}}, tmp_path) is None
