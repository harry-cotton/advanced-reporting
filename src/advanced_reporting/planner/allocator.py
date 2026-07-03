"""Deterministic budget split — the numbers originate HERE, never the LLM.

``allocate`` optimizes the channel split against the MMM response curves subject to the rails
(per-channel min/max, total reconciles), then pushes each channel budget down to its audiences
and creatives. With a fitted MMM it does **marginal-return water-filling** (concave curves ->
diversified, diminishing-returns-aware split). With no MMM it falls back to a rules split (even
or prior-weighted by historical ROAS), explicitly flagged low-confidence.
"""
from __future__ import annotations

import numpy as np

from .rails import channel_bounds
from .schema import CampaignPlan, Recommendation

_CURVE_CONF = 0.8        # confidence when the split is MMM-curve grounded
_RULES_CONF = 0.3        # confidence for the no-MMM rules fallback


def _platform_nodes(plan: CampaignPlan):
    """All platform nodes as ``(stage, platform)`` pairs (one allocatable unit per channel)."""
    return [(st, pf) for st in plan.stages for pf in st.platforms]


def _marginal_fn(curves: dict, channel: str):
    """A callable s -> d(response)/d(spend) at spend ``s`` for ``channel`` (or None)."""
    c = (curves or {}).get(channel)
    if not c:
        return None
    spend = np.asarray(c["spend"], dtype=float)
    resp = np.asarray(c["response"], dtype=float)
    if spend.size < 2:
        return None
    deriv = np.gradient(resp, spend)
    smin, smax = float(spend.min()), float(spend.max())
    return lambda s: float(np.interp(min(max(s, smin), smax), spend, deriv))


def _waterfill(channels, total, lo, hi, curves) -> list[float]:
    """Greedily add budget to the channel with the highest current marginal return."""
    n = len(channels)
    eff_lo = min(lo, total / n) if n else 0.0
    alloc = [eff_lo] * n
    remaining = total - eff_lo * n
    step = max(total / 1000.0, 1e-9)
    marg = [_marginal_fn(curves, ch) for ch in channels]

    guard = 0
    while remaining > 1e-6 and guard < 500_000:
        guard += 1
        best_i, best_m = None, -np.inf
        for i in range(n):
            if alloc[i] >= hi - 1e-9:
                continue
            m = marg[i](alloc[i]) if marg[i] else 0.0
            if m > best_m:
                best_m, best_i = m, i
        if best_i is None:                       # everyone at max
            break
        add = min(step, remaining, hi - alloc[best_i])
        if add <= 0:
            break
        alloc[best_i] += add
        remaining -= add

    if remaining > 1e-6:                         # all hit max: spread leftover (validate clips)
        for i in range(n):
            alloc[i] += remaining / n
    return alloc


def _rules_split(channels, total, lo, hi, strategy, priors) -> list[float]:
    """Even or prior-weighted (by historical ROAS) split, clipped + renormalized to total."""
    n = len(channels)
    if strategy == "prior_weighted" and priors:
        w = np.array([max(float(priors.get(ch, 0.0)), 0.0) for ch in channels], dtype=float)
        raw = (total * w / w.sum()) if w.sum() > 0 else np.full(n, total / n)
    else:
        raw = np.full(n, total / n)
    return _clip_renormalize(list(raw), total, lo, hi)


def _clip_renormalize(alloc, total, lo, hi) -> list[float]:
    """Clip each share to [lo, hi], then rescale the free room so the sum returns to total."""
    a = [min(max(x, lo), hi) for x in alloc]
    for _ in range(50):
        diff = total - sum(a)
        if abs(diff) < 1e-6:
            break
        # channels that can still move in the needed direction
        movable = [i for i in range(len(a)) if (diff > 0 and a[i] < hi) or (diff < 0 and a[i] > lo)]
        if not movable:
            break
        share = diff / len(movable)
        for i in movable:
            a[i] = min(max(a[i] + share, lo), hi)
    return a


def _push_down(plan: CampaignPlan):
    """Distribute each platform budget evenly across its audiences, then creatives; sum stages."""
    for st in plan.stages:
        st_total = 0.0
        for pf in st.platforms:
            pf_budget = pf.budget or 0.0
            st_total += pf_budget
            auds = pf.audiences or []
            per_aud = pf_budget / len(auds) if auds else 0.0
            for au in auds:
                au.budget = per_aud
                crs = au.creatives or []
                per_cr = per_aud / len(crs) if crs else 0.0
                for cr in crs:
                    cr.budget = per_cr
        st.budget = st_total


def allocate(plan: CampaignPlan, rails: dict, *, curves: dict | None = None,
             priors: dict | None = None) -> CampaignPlan:
    """Set ``budget`` on every node of ``plan`` deterministically. Returns the same plan."""
    nodes = _platform_nodes(plan)
    if not nodes:
        return plan
    channels = [pf.channel for _, pf in nodes]
    total = float(plan.total_budget)
    lo, hi = channel_bounds(rails, total)

    have_curves = bool(curves) and any(ch in curves for ch in channels)
    if have_curves:
        alloc = _waterfill(channels, total, lo, hi, curves)
        source, conf, ref = "mmm_response_curves", _CURVE_CONF, "response_curves"
        note = "Split optimized against MMM response curves (marginal return)."
    else:
        strategy = rails.get("budget_rules", {}).get("no_mmm_strategy", "even")
        alloc = _rules_split(channels, total, lo, hi, strategy, priors)
        source, conf, ref = f"rules:{strategy}", _RULES_CONF, "historical:channel"
        note = (f"No fitted MMM available — {strategy} rules split, low confidence. "
                "Fit an MMM for incrementality-grounded allocation.")

    for (st, pf), budget in zip(nodes, alloc):
        pf.budget = float(budget)
        pf.rec = Recommendation(rationale=note, evidence_ref=f"{ref}:{pf.channel}",
                                confidence=conf)

    _push_down(plan)
    # carry the allocation confidence onto the plan trace so it surfaces downstream
    plan.trace.notes = (plan.trace.notes + " " if plan.trace.notes else "") + \
        f"allocation={source} (confidence {conf})."
    return plan
