"""Rails enforcement — never trust the LLM to self-police.

``check`` lists every way a ``CampaignPlan`` violates the rails or is incomplete. ``enforce``
repairs what it can (drop disallowed channels, trim over-cap audiences/creatives, strip blocked
placements, clip budgets to bounds and renormalize) and raises ``PlannerValidationError`` for
anything left unrepairable.

The per-platform audience cap (``caps.max_audiences_per_stage``) is checked per platform node —
a platform is a channel's slot within a funnel stage.
"""
from __future__ import annotations

from .allocator import _clip_renormalize, _push_down
from .rails import allowed_channels, channel_bounds, reconcile_tolerance
from .schema import CampaignPlan, PlannerValidationError  # noqa: F401 — re-exported


def _libraries(rails: dict) -> tuple[set, set]:
    """The rails vocabulary as membership sets: audience (type, detail) pairs and
    creative (creative, format, size) triples, across all goals/objectives."""
    lib_aud = {(a.get("audience_type"), a.get("audience_detail"))
               for entries in (rails.get("audience_library") or {}).values()
               for a in (entries or [])}
    lib_cr = {(c.get("creative"), c.get("format"), c.get("size"))
              for entries in (rails.get("creatives") or {}).values()
              for c in (entries or [])}
    return lib_aud, lib_cr


def _channel_totals(plan: CampaignPlan) -> dict:
    totals: dict = {}
    for st in plan.stages:
        for pf in st.platforms:
            totals[pf.channel] = totals.get(pf.channel, 0.0) + (pf.budget or 0.0)
    return totals


def check(plan: CampaignPlan, rails: dict) -> list[str]:
    """Return a list of rails/completeness violations (empty == valid)."""
    issues: list[str] = []
    allowed = set(allowed_channels(rails))
    caps = rails.get("caps", {})
    blocked = set(rails.get("brand_safety", {}).get("blocked_placements", []))
    total = float(plan.total_budget)
    lo, hi = channel_bounds(rails, total)
    tol = reconcile_tolerance(rails) * max(total, 1.0)

    # completeness — campaign meta
    if not plan.market:
        issues.append("campaign: market is empty")
    if not plan.primary_kpi:
        issues.append("campaign: primary_kpi is empty")
    if total <= 0:
        issues.append("campaign: total_budget must be > 0")

    creatives = list(plan.iter_creatives())
    if not creatives:
        issues.append("plan has no creatives")

    # structure — caps + allowed vocab + brand safety + completeness of leaves
    channels = set()
    for st in plan.stages:
        for pf in st.platforms:
            channels.add(pf.channel)
            if pf.channel not in allowed:
                issues.append(f"channel '{pf.channel}' is not in allowed platforms")
            if len(pf.audiences) > int(caps.get("max_audiences_per_stage", 1_000)):
                issues.append(f"channel '{pf.channel}': {len(pf.audiences)} audiences exceeds "
                              f"cap {caps.get('max_audiences_per_stage')}")
            for au in pf.audiences:
                if au.placement and au.placement in blocked:
                    issues.append(f"placement '{au.placement}' is brand-unsafe (blocked)")
                if len(au.creatives) > int(caps.get("max_creatives_per_audience", 1_000)):
                    issues.append(f"audience '{au.audience_detail}': {len(au.creatives)} creatives "
                                  f"exceeds cap {caps.get('max_creatives_per_audience')}")
    if len(channels) > int(caps.get("max_channels", 1_000)):
        issues.append(f"{len(channels)} channels exceeds cap {caps.get('max_channels')}")

    # vocabulary: audiences/creatives must come from the rails libraries. The LLM clip
    # (_clip_selection) guards only the LLM path — a hand-built plan with invented
    # entries used to sail through check() and into the naming generator.
    lib_aud, lib_cr = _libraries(rails)
    for st in plan.stages:
        for pf in st.platforms:
            for au in pf.audiences:
                if lib_aud and (au.audience_type, au.audience_detail) not in lib_aud:
                    issues.append(f"audience ('{au.audience_type}', '{au.audience_detail}') "
                                  "is not in the rails audience library")
                for cr in au.creatives:
                    if lib_cr and (cr.creative, cr.format, cr.size) not in lib_cr:
                        issues.append(f"creative ('{cr.creative}', '{cr.format}', "
                                      f"'{cr.size}') is not in the rails creative library")

    # completeness of leaves
    for st, pf, au, cr in creatives:
        if not (cr.format and cr.size):
            issues.append(f"creative '{cr.creative}' missing format/size")
    for st in plan.stages:
        for pf in st.platforms:
            if pf.budget is None:
                issues.append(f"channel '{pf.channel}' has no budget")

    # budget bounds (per-channel total) + reconciliation
    for ch, amt in _channel_totals(plan).items():
        if amt + 1e-6 < lo or amt > hi + 1e-6:
            issues.append(f"channel '{ch}' budget {amt:,.0f} outside bounds "
                          f"[{lo:,.0f}, {hi:,.0f}]")
    leaf_sum = sum((cr.budget or 0.0) for *_x, cr in creatives)
    if creatives and abs(leaf_sum - total) > tol:
        issues.append(f"budgets sum to {leaf_sum:,.0f}, not total {total:,.0f} "
                      f"(tolerance {tol:,.0f})")
    return issues


def enforce(plan: CampaignPlan, rails: dict) -> CampaignPlan:
    """Repair what's repairable; raise ``PlannerValidationError`` for what isn't."""
    allowed = set(allowed_channels(rails))
    caps = rails.get("caps", {})
    blocked = set(rails.get("brand_safety", {}).get("blocked_placements", []))
    max_aud = int(caps.get("max_audiences_per_stage", 1_000))
    max_cr = int(caps.get("max_creatives_per_audience", 1_000))
    max_ch = int(caps.get("max_channels", 1_000))

    # 1. structural repairs: drop disallowed channels and out-of-library audiences/
    #    creatives; trim caps; strip blocked placements
    lib_aud, lib_cr = _libraries(rails)
    seen_channels: list[str] = []
    for st in plan.stages:
        kept_platforms = []
        for pf in st.platforms:
            if pf.channel not in allowed:
                continue
            if pf.channel not in seen_channels:
                if len(seen_channels) >= max_ch:
                    continue                       # over the channel cap -> drop
                seen_channels.append(pf.channel)
            pf.audiences = [au for au in pf.audiences
                            if not (au.placement and au.placement in blocked)
                            and (not lib_aud
                                 or (au.audience_type, au.audience_detail) in lib_aud)
                            ][:max_aud]
            for au in pf.audiences:
                au.creatives = [cr for cr in au.creatives
                                if not lib_cr
                                or (cr.creative, cr.format, cr.size) in lib_cr][:max_cr]
            kept_platforms.append(pf)
        st.platforms = kept_platforms
    plan.stages = [st for st in plan.stages if st.platforms]

    # 2. budget repair: clip per-channel totals to bounds, renormalize, push back down
    nodes = [(st, pf) for st in plan.stages for pf in st.platforms]
    if nodes:
        total = float(plan.total_budget)
        lo, hi = channel_bounds(rails, total)
        clipped = _clip_renormalize([pf.budget or 0.0 for _, pf in nodes], total, lo, hi)
        for (_, pf), b in zip(nodes, clipped):
            pf.budget = float(b)
        _push_down(plan)

    # 3. anything still broken is unrepairable
    remaining = check(plan, rails)
    if remaining:
        raise PlannerValidationError("plan violates rails: " + "; ".join(remaining))
    return plan
