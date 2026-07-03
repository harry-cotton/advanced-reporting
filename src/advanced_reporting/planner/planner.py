"""The thin planner core: goals + rails -> validated ``CampaignPlan``.

``plan_campaign`` (1) gathers evidence deterministically, (2) proposes the *qualitative* plan
(funnel objective, which channels, which audiences, which creative angles) — by rules by
default, or via one guarded LLM call when ``ANTHROPIC_API_KEY`` is set — (3) lets the
deterministic ``allocator`` set every budget, and (4) enforces the rails. The LLM only ever
*selects from* the rails (channels/audiences/creatives are clipped to the allowed sets); it
never invents options and never touches a number. Mirrors ``reporting/lens.py``'s guarded path,
and adds the trace + token-cost metric the brief requires.
"""
from __future__ import annotations

import json
import os

from ..reporting import metrics as M
from . import allocator, evidence, validate
from .rails import allowed_channels, funnel_for_goal, load_rails
from .schema import (Audience, CampaignPlan, Creative, FunnelStage, Platform,
                     PlannerTrace, Recommendation)

_LLM_CONF = 0.7
_DET_CONF = 0.5


# --- model access (single swap point: Bedrock / Vertex / direct API) -------------------

def _llm_call(prompt: str, *, model: str, max_tokens: int):
    """Make one Anthropic call; return ``(text, usage)``. The only place a model is invoked.

    Swap the client here to target Bedrock/Vertex without touching the rest of the planner.
    Imported lazily so ``anthropic`` stays an optional dependency (deterministic path needs none).
    """
    import anthropic  # lazy: only imported on the guarded LLM path

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text, msg.usage


def _use_llm(use_llm) -> bool:
    if use_llm is not None:
        return bool(use_llm)
    return bool(os.getenv("ANTHROPIC_API_KEY"))


# --- goals normalization ---------------------------------------------------------------

def _normalize_goals(goals: dict, rails: dict) -> dict:
    """Fill campaign-meta defaults from the rails and resolve the goal if not given."""
    if "total_budget" not in goals:
        raise ValueError("goals must include 'total_budget'")
    g = dict(goals)
    camp = rails.get("campaign", {})
    g.setdefault("client", "")
    g.setdefault("market", camp.get("default_market", "US"))
    g.setdefault("campaign", g.get("goal", "campaign"))
    g.setdefault("flight_start", "")
    g.setdefault("flight_end", "")
    g.setdefault("primary_kpi", "conversions")
    g.setdefault("version", camp.get("default_version", "V1"))
    if "goal" not in g or not g["goal"]:
        g["goal"] = M.resolve_goal(g["campaign"], M.load_campaign_goals())
    g["total_budget"] = float(g["total_budget"])
    return g


# --- qualitative proposers -------------------------------------------------------------

def _library_audiences(rails: dict, goal: str) -> list[dict]:
    cap = int(rails.get("caps", {}).get("max_audiences_per_stage", 1_000))
    lib = rails.get("audience_library", {}).get(goal, [])
    return [dict(a) for a in lib][:cap] or [
        {"audience_type": "PROSPECT", "audience_detail": "BROAD", "placement": "FEED"}]


def _library_creatives(rails: dict, objective: str) -> list[dict]:
    cap = int(rails.get("caps", {}).get("max_creatives_per_audience", 1_000))
    lib = rails.get("creatives", {}).get(objective, [])
    return [dict(c) for c in lib][:cap] or [
        {"creative": "GENERIC", "format": "STATIC", "size": "1x1"}]


def _select_channels(rails: dict, goal: str, preferred=None) -> list[str]:
    cap = int(rails.get("caps", {}).get("max_channels", 1_000))
    allowed = allowed_channels(rails)
    lead = funnel_for_goal(rails, goal).get("lead_channels", [])
    pick = [c for c in (preferred or lead) if c in allowed]
    if not pick:
        pick = list(allowed)
    # de-dup, preserve order, cap
    seen, out = set(), []
    for c in pick:
        if c not in seen:
            seen.add(c); out.append(c)
    return out[:cap]


def _propose_deterministic(g: dict, rails: dict) -> dict:
    """Rules proposer: funnel + audiences + creatives straight from the rails (no LLM, no key)."""
    goal = g["goal"]
    objective = funnel_for_goal(rails, goal).get("objective", "CONVERT")
    return {
        "goal": goal,
        "objective": objective,
        "channels": _select_channels(rails, goal, g.get("channels")),
        "audiences": _library_audiences(rails, goal),
        "creatives": _library_creatives(rails, objective),
        "rationale": f"Deterministic {goal} plan from rails (objective {objective}).",
    }


def _build_prompt(g: dict, rails: dict, ev: dict) -> str:
    """Compact, structured prompt — goals + rails + per-channel evidence ONLY (never raw rows)."""
    goal = g["goal"]
    objective = funnel_for_goal(rails, goal).get("objective", "CONVERT")
    hist = (ev.get("historical").data if ev.get("historical") else {})
    curves = (ev.get("curves").data if ev.get("curves") else {})
    ev_lines = []
    for ch in allowed_channels(rails):
        h, c = hist.get(ch, {}), curves.get(ch, {})
        ev_lines.append(
            f"  {ch}: roas={h.get('roas', float('nan')):.2f} cpa={h.get('cpa', float('nan')):.2f} "
            f"cvr={h.get('cvr', float('nan')):.3f} "
            f"marginal_return={c.get('marginal_return_at_mean', float('nan')):.3f} "
            f"roi={c.get('roi', float('nan')):.2f}")
    payload = {
        "goal": goal, "objective": objective,
        "allowed_channels": allowed_channels(rails),
        "audience_library": rails.get("audience_library", {}).get(goal, []),
        "creative_library": rails.get("creatives", {}).get(objective, []),
        "caps": rails.get("caps", {}),
    }
    return (
        "You are a media planner. Select (do NOT invent) a campaign structure from the rails "
        "below for the stated goal, justified by the per-channel evidence. Choose a subset of "
        "allowed_channels, a subset of audience_library, and a subset of creative_library, all "
        "within caps. Do NOT propose any budgets.\n\n"
        f"GOAL/RAILS:\n{json.dumps(payload, indent=2)}\n\n"
        f"EVIDENCE (per channel):\n" + "\n".join(ev_lines) + "\n\n"
        'Return ONLY JSON: {"channels": [...], "audiences": [{"audience_type","audience_detail",'
        '"placement"}], "creatives": [{"creative","format","size"}], "rationale": "..."}'
    )


def _clip_selection(data: dict, g: dict, rails: dict) -> dict:
    """Clip an LLM selection to the rails: allowed channels, library audiences/creatives, caps.

    Anything the LLM invented (not in the library) is dropped — the LLM selects, never invents.
    """
    goal = g["goal"]
    objective = funnel_for_goal(rails, goal).get("objective", "CONVERT")
    lib_aud = {(a["audience_type"], a["audience_detail"]): dict(a)
               for a in rails.get("audience_library", {}).get(goal, [])}
    lib_cr = {(c["creative"], c["format"], c["size"]): dict(c)
              for c in rails.get("creatives", {}).get(objective, [])}
    caps = rails.get("caps", {})

    channels = _select_channels(rails, goal, [c for c in (data.get("channels") or [])])
    audiences = []
    for a in (data.get("audiences") or []):
        key = (a.get("audience_type"), a.get("audience_detail"))
        if key in lib_aud:
            audiences.append(dict(lib_aud[key]))
    audiences = audiences[:int(caps.get("max_audiences_per_stage", 1_000))] \
        or _library_audiences(rails, goal)
    creatives = []
    for c in (data.get("creatives") or []):
        key = (c.get("creative"), c.get("format"), c.get("size"))
        if key in lib_cr:
            creatives.append(dict(lib_cr[key]))
    creatives = creatives[:int(caps.get("max_creatives_per_audience", 1_000))] \
        or _library_creatives(rails, objective)
    return {"goal": goal, "objective": objective, "channels": channels,
            "audiences": audiences, "creatives": creatives,
            "rationale": str(data.get("rationale", ""))[:500]}


def _propose_with_llm(g: dict, rails: dict, ev: dict):
    """One guarded LLM call -> clipped spec + trace; returns ``(None, None)`` on any failure."""
    try:
        prompt = _build_prompt(g, rails, ev)
        llm = rails.get("llm", {})
        text, usage = _llm_call(prompt, model=llm.get("model", "claude-opus-4-8"),
                                max_tokens=int(llm.get("max_tokens", 1500)))
        data = json.loads(text[text.index("{"):text.rindex("}") + 1])
        spec = _clip_selection(data, g, rails)
        trace = _llm_trace(rails, usage, spec, ev)
        return spec, trace
    except Exception:
        return None, None


# --- traces ----------------------------------------------------------------------------

def _evidence_refs(ev: dict) -> list[str]:
    refs = []
    for e in ev.values():
        if e is not None:
            refs.extend(getattr(e, "refs", []))
    return refs


def _llm_trace(rails: dict, usage, spec: dict, ev: dict) -> PlannerTrace:
    pricing = rails.get("llm", {}).get("pricing", {})
    inp = int(getattr(usage, "input_tokens", 0) or 0)
    out = int(getattr(usage, "output_tokens", 0) or 0)
    cost = inp / 1e6 * float(pricing.get("input", 0.0)) + out / 1e6 * float(pricing.get("output", 0.0))
    return PlannerTrace(
        source="llm", model=rails.get("llm", {}).get("model"),
        input_tokens=inp, output_tokens=out, context_tokens=inp, cost_usd=cost,
        confidence=_LLM_CONF, evidence_refs=_evidence_refs(ev), choice=spec,
        notes="qualitative selection by guarded LLM (clipped to rails).")


def _det_trace(spec: dict, ev: dict) -> PlannerTrace:
    return PlannerTrace(source="deterministic", model=None, confidence=_DET_CONF,
                        evidence_refs=_evidence_refs(ev), choice=spec,
                        notes="qualitative selection by deterministic rules.")


# --- assembly --------------------------------------------------------------------------

def _assemble_plan(g: dict, rails: dict, spec: dict, trace: PlannerTrace) -> CampaignPlan:
    """Build the ``CampaignPlan`` skeleton from a qualitative spec (no budgets yet)."""
    stage = FunnelStage(
        objective=spec["objective"],
        rec=Recommendation(rationale=spec.get("rationale", ""), evidence_ref="funnel",
                           confidence=trace.confidence))
    for ch in spec["channels"]:
        pf = Platform(channel=ch)
        for a in spec["audiences"]:
            au = Audience(audience_type=a["audience_type"],
                          audience_detail=a["audience_detail"],
                          placement=a.get("placement", ""))
            for c in spec["creatives"]:
                au.creatives.append(Creative(creative=c["creative"], format=c["format"],
                                             size=c["size"], version=g["version"]))
            pf.audiences.append(au)
        stage.platforms.append(pf)
    plan = CampaignPlan(
        client=g["client"], market=g["market"], campaign=g["campaign"],
        flight_start=g["flight_start"], flight_end=g["flight_end"],
        total_budget=g["total_budget"], primary_kpi=g["primary_kpi"],
        stages=[stage], trace=trace)
    return plan


# --- public entry point ----------------------------------------------------------------

def plan_campaign(goals: dict, rails: dict | None = None, *, history=None,
                  mmm_result=None, use_llm=None) -> CampaignPlan:
    """Turn ``goals`` (+ optional store history / fitted MMM) into a validated ``CampaignPlan``."""
    rails = load_rails() if rails is None else rails
    g = _normalize_goals(goals, rails)

    # 1. gather evidence (deterministic, compact)
    hist_ev = None
    if history is not None:
        hist_ev = evidence.historical_performance(history)
    else:
        try:
            hist_ev = evidence.historical_performance()
        except FileNotFoundError:
            hist_ev = None
    curves_ev = evidence.response_curves(mmm_result) if mmm_result is not None else None
    ev = {"historical": hist_ev, "curves": curves_ev}

    # 2. propose the qualitative plan (guarded LLM, else rules)
    spec, trace = (None, None)
    if _use_llm(use_llm):
        spec, trace = _propose_with_llm(g, rails, ev)
    if spec is None:
        spec = _propose_deterministic(g, rails)
        trace = _det_trace(spec, ev)

    # 3. assemble skeleton, 4. allocator sets budgets, 5. enforce rails
    plan = _assemble_plan(g, rails, spec, trace)
    priors = None
    if hist_ev:
        priors = {ch: rec["roas"] for ch, rec in hist_ev.data.items()
                  if isinstance(rec.get("roas"), float) and rec["roas"] == rec["roas"]}
    allocator.allocate(
        plan, rails,
        curves=(mmm_result.response_curves if mmm_result is not None else None),
        priors=priors)
    return validate.enforce(plan, rails)
