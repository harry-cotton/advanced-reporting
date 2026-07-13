"""Deterministic recommendation eligibility — the rails for A2.

``system/guidelines/recommendation_menu.md`` is the contract: deterministic code
(this module) computes which recommendation types are ELIGIBLE and the evidence each
must cite; the commentary agent may only select, order and justify from that list.
It can never invent a type, a number, or a budget.

``rebalance_channel_budget`` is intentionally NOT computed yet: the menu requires it
to cite allocator numbers ONLY, and the planner-allocator wiring into the reporting
path hasn't been built — so it is never eligible rather than eligible-with-made-up
numbers. (Wire ``planner/allocator.py`` against the persisted response curves to
unlock it.)
"""
from __future__ import annotations

import pandas as pd

from ..dashboard.insights import _money, _paid_channels, channel_label

MAX_RECS = 3          # recommendation_menu.md: max 3 per report, by money at stake

# conversion_types.md starting tripwires (monitor & tighten per client)
CLAIM_RATIO_LOW = 0.9
CLAIM_RATIO_HIGH = 3.5
UNPARSED_SPEND_THRESHOLD = 0.10   # fix_naming trigger: >10% of ad-level spend
SHIFT_MIN_MULT = 2.0              # shift_within_type: >=2x cost gap
SHIFT_MIN_SPEND_SHARE = 0.05      # ...with material spend: each side >=5% of ad spend

EVIDENCE_GRADES = ("platform-claimed", "analytics-measured", "modeled")


def _rec(rtype: str, grade: str, evidence: dict, summary: str) -> dict:
    return {"type": rtype, "evidence_grade": grade, "evidence": evidence,
            "summary": summary}


def _investigate_tracking(weekly: pd.DataFrame) -> list[dict]:
    if "key_events" not in weekly or "conversions" not in weekly:
        return []
    paid = weekly[weekly["channel"].isin(_paid_channels(weekly))]
    per = (paid.groupby("channel")[["conversions", "key_events"]].sum(min_count=1)
           .dropna())
    per = per[per["key_events"] > 0]
    out = []
    for ch, row in per.iterrows():
        ratio = float(row["conversions"]) / float(row["key_events"])
        if ratio < CLAIM_RATIO_LOW or ratio > CLAIM_RATIO_HIGH:
            direction = ("platforms claim LESS than analytics measures — usually a "
                         "tracking break, not performance" if ratio < CLAIM_RATIO_LOW
                         else "claims are mostly attribution artifacts — check event "
                              "dedup, view-through windows, tag coverage")
            out.append(_rec(
                "investigate_tracking", "analytics-measured",
                {"channel": str(ch), "claim_ratio": f"{ratio:.1f}x",
                 "normal_band": f"{CLAIM_RATIO_LOW}-{CLAIM_RATIO_HIGH}x"},
                f"{channel_label(str(ch))} claim ratio {ratio:.1f}x is outside the "
                f"{CLAIM_RATIO_LOW}-{CLAIM_RATIO_HIGH}x band; {direction}. Worded as "
                "a measurement issue, never as performance."))
    return out


def _fix_naming(unparsed: dict | None) -> list[dict]:
    if not unparsed or unparsed.get("spend_rate", 0.0) <= UNPARSED_SPEND_THRESHOLD:
        return []
    rate = unparsed["spend_rate"]
    names = unparsed.get("names") or []
    return [_rec(
        "fix_naming", "platform-claimed",
        {"unparsed_spend_share": f"{rate * 100:.0f}%",
         "offending_names": names[:8]},
        f"{rate * 100:.0f}% of ad-level spend carries names the convention can't "
        f"decode ({len(names)} distinct names) — that spend is invisible to audience/"
        "creative analysis until renamed.")]


def _shift_within_type(hist: pd.DataFrame | None) -> list[dict]:
    """Two audiences of the SAME type >=2x apart on cost per claimed conversion,
    both with material spend. Cross-type comparisons are structurally banned (warm
    beats cold by construction)."""
    if hist is None or "audience_type" not in getattr(hist, "columns", ()):
        return []
    from ..ingestion.naming_decode import UNPARSED
    ad = hist[(hist.get("ad_group", "") != "") & (hist["audience_type"] != "")
              & (hist["audience_type"] != UNPARSED)]
    if ad.empty:
        return []
    per = (ad.groupby(["audience_type", "audience_detail"])[["spend", "conversions"]]
           .sum(min_count=1).dropna().reset_index())
    per = per[(per["conversions"] > 0) & (per["spend"] > 0)]
    total_spend = float(per["spend"].sum())
    if total_spend <= 0:
        return []
    per["cost_per_claimed"] = per["spend"] / per["conversions"]
    out = []
    for atype, grp in per.groupby("audience_type", sort=False):
        grp = grp[grp["spend"] >= SHIFT_MIN_SPEND_SHARE * total_spend]
        if len(grp) < 2:
            continue
        grp = grp.sort_values("cost_per_claimed")
        best, worst = grp.iloc[0], grp.iloc[-1]
        mult = worst["cost_per_claimed"] / best["cost_per_claimed"]
        if mult < SHIFT_MIN_MULT:
            continue
        out.append(_rec(
            "shift_within_type", "platform-claimed",
            {"audience_type": str(atype),
             "better": {"audience": str(best["audience_detail"]),
                        "cost_per_claimed": _money(best["cost_per_claimed"]),
                        "spend": _money(best["spend"])},
             "worse": {"audience": str(worst["audience_detail"]),
                       "cost_per_claimed": _money(worst["cost_per_claimed"]),
                       "spend": _money(worst["spend"])},
             "gap": f"{mult:.1f}x"},
            f"Within {atype} audiences, {best['audience_detail']} converts at "
            f"{_money(best['cost_per_claimed'])} vs {worst['audience_detail']} at "
            f"{_money(worst['cost_per_claimed'])} ({mult:.1f}x gap, both with "
            "material spend). All figures platform-claimed."))
    return out


def _headroom(curves: dict, ch: str) -> tuple[float, float] | None:
    """(mean_weekly_spend, curve_midpoint) when spend sits below the response-curve
    midpoint — the shared 'room to scale' heuristic; None otherwise."""
    curve = curves.get(ch) or {}
    spend_grid = curve.get("spend") or []
    mean_spend = curve.get("mean_spend")
    if mean_spend is None or not spend_grid:
        return None
    midpoint = max(spend_grid) / 2
    return (float(mean_spend), midpoint) if float(mean_spend) < midpoint else None


def _mmm_recs_count(mmm: dict) -> list[dict]:
    """Count-target verdicts: grade COST PER INCREMENTAL OUTCOME against the client
    band — never ROI-vs-1.0, which every count channel fails by construction
    (apps/$ ≈ 0.005; the P3 verdict decision)."""
    from ..dashboard.mmm_view import cost_per_outcome_intervals, response_curves
    meta = mmm.get("meta") or {}
    cpo = cost_per_outcome_intervals(mmm["summary"], meta)
    curves = response_curves(meta)
    good, warn = float(cpo["good"].iloc[0]), float(cpo["warn"].iloc[0])
    out = []
    for _, r in cpo.iterrows():
        ch = str(r["channel"])
        if r["verdict"] == "cut_candidate":       # finite interval, entirely above warn
            ev = {"channel": ch,
                  "cost_per_incremental_outcome": _money(float(r["cost_per"])),
                  "cost_interval_90": f"{_money(float(r['cost_low']))}-"
                                      f"{_money(float(r['cost_high']))}",
                  "client_band": f"good <= {_money(good)}, watch <= {_money(warn)}"}
            out.append(_rec(
                "cut_or_restructure", "modeled", ev,
                f"{channel_label(ch)} costs {_money(float(r['cost_per']))} per "
                f"incremental outcome ({_money(float(r['cost_low']))}-"
                f"{_money(float(r['cost_high']))}, 90% interval) — even the best case "
                f"sits above the client's {_money(warn)} watch line at current spend."))
        elif r["verdict"] == "strong":            # whole interval beats the good band
            head = _headroom(curves, ch)
            if head is None:
                continue
            mean_spend, midpoint = head
            ev = {"channel": ch,
                  "cost_per_incremental_outcome": _money(float(r["cost_per"])),
                  "cost_interval_90": f"{_money(float(r['cost_low']))}-"
                                      f"{_money(float(r['cost_high']))}",
                  "client_band": f"good <= {_money(good)}, watch <= {_money(warn)}",
                  "mean_weekly_spend": _money(mean_spend),
                  "saturation_midpoint": _money(midpoint)}
            out.append(_rec(
                "scale_with_test", "modeled", ev,
                f"{channel_label(ch)} delivers an incremental outcome for "
                f"{_money(float(r['cost_per']))} ({_money(float(r['cost_low']))}-"
                f"{_money(float(r['cost_high']))}, 90% interval) — beating the "
                f"{_money(good)} good band even in the worst case — and spend "
                f"({_money(mean_spend)}/wk) sits below the response-curve midpoint "
                f"({_money(midpoint)}): headroom worth a controlled test."))
        # unproven (spans the band, or the model can't rule out zero effect): no rec.
    return out


def _mmm_recs(mmm: dict | None) -> list[dict]:
    """scale_with_test / cut_or_restructure from the persisted MMMResult; modeled
    evidence, hedged by construction (whole-interval conditions only)."""
    if not mmm:
        return []
    from ..dashboard.mmm_view import is_count_target, response_curves, roi_intervals
    if is_count_target(mmm.get("meta") or {}):
        return _mmm_recs_count(mmm)
    out = []
    intervals = roi_intervals(mmm["summary"])
    curves = response_curves(mmm.get("meta") or {})
    for _, r in intervals.iterrows():
        ch = str(r["channel"])
        lo, hi, roi = float(r["roi_low"]), float(r["roi_high"]), float(r["roi"])
        ev = {"channel": ch, "roi": f"{roi:.2f}",
              "roi_interval_90": f"{lo:.2f}-{hi:.2f}"}
        if hi < 1.0:
            out.append(_rec(
                "cut_or_restructure", "modeled", ev,
                f"{channel_label(ch)} 90% ROI interval {lo:.2f}-{hi:.2f} sits "
                "entirely below 1 — the model can't find a profitable story at "
                "current spend."))
        elif lo >= 1.0:
            curve = curves.get(ch) or {}
            spend_grid = curve.get("spend") or []
            mean_spend = curve.get("mean_spend")
            if mean_spend is None or not spend_grid:
                continue
            midpoint = max(spend_grid) / 2  # heuristic: half the modeled spend range
            if float(mean_spend) < midpoint:
                ev["mean_weekly_spend"] = _money(float(mean_spend))
                ev["saturation_midpoint"] = _money(midpoint)
                out.append(_rec(
                    "scale_with_test", "modeled", ev,
                    f"{channel_label(ch)} 90% ROI interval {lo:.2f}-{hi:.2f} is "
                    f"entirely above 1 and spend ({_money(float(mean_spend))}/wk) "
                    f"sits below the response-curve midpoint "
                    f"({_money(midpoint)}) — headroom worth a controlled test."))
    return out


def _unlock_mmm(mmm: dict | None, weekly: pd.DataFrame) -> list[dict]:
    if mmm:
        return []
    return [_rec(
        "unlock_mmm", "analytics-measured",
        {"missing": "business-KPI weekly series (e.g. CRM matchback)"},
        "No business-KPI series exists, so incrementality modeling (MMM) is off — "
        "everything above is descriptive. A CRM matchback (applications, enrollments) "
        "would unlock budget optimization against modeled response curves.")]


def eligible_recommendations(weekly: pd.DataFrame, hist: pd.DataFrame | None = None,
                             mmm: dict | None = None,
                             unparsed: dict | None = None) -> list[dict]:
    """Every recommendation candidate the computed data makes ELIGIBLE, tagged with
    its menu type, evidence grade and required citations. The agent selects, orders
    (by money at stake) and justifies from this list only."""
    recs: list[dict] = []
    recs += _investigate_tracking(weekly)
    recs += _fix_naming(unparsed)
    recs += _shift_within_type(hist)
    recs += _mmm_recs(mmm)
    recs += _unlock_mmm(mmm, weekly)
    return recs
