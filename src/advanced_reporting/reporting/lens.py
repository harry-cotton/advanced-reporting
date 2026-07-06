"""Free-text report lens: a plain-English intent reshapes the report.

``parse_lens`` turns text like "this is an awareness campaign, focus on Meta and TikTok"
into a ``ReportSpec`` (goal, primary tier, featured metrics, channels, tone). The numbers are
ALWAYS computed in code (``reporting/metrics.py``); the lens only decides WHICH tier leads,
WHICH metrics/channels surface, and the framing of the narrative. An optional LLM parse kicks
in when ``ANTHROPIC_API_KEY`` is set; the deterministic path needs no key or network and is the
tested default.

Imports only ``metrics`` + ``utils`` (no ingestion/transform), so it stays light and testable.
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass

import pandas as pd

from . import metrics as M
from ..utils import load_config, load_mappings, phrase_in


@dataclass
class ReportSpec:
    goal: str
    primary_tier: str
    metrics: list                 # featured metric keys, primary tier first
    channels: list | None = None  # None = all channels
    tone: str = "standard"        # standard | executive | detailed
    source_text: str = ""


_TONE_PATTERNS = {
    "executive": ["exec", "executive", "brief", "short", "summary", "tldr", "tl;dr",
                  "high level", "one pager"],
    "detailed": ["detail", "detailed", "deep", "deep dive", "thorough", "comprehensive",
                 "granular", "full breakdown"],
}


def _detect_tone(text: str) -> str:
    # whole-word/phrase matching ('brief' no longer fires inside 'debrief'), and an
    # explicit detail request outranks a generic 'summary' — 'give me a detailed
    # summary' used to silently produce the thinnest report
    if any(phrase_in(text, p) for p in _TONE_PATTERNS["detailed"]):
        return "detailed"
    if any(phrase_in(text, p) for p in _TONE_PATTERNS["executive"]):
        return "executive"
    return "standard"


# Aliases that exist for COLUMN mapping but are too short or too generic to trust in
# free text: 'ig' fired inside 'campa-ig-n' — any text mentioning "campaign" (including
# the dashboard's own placeholder) silently filtered everything to Meta; 'search' fired
# inside search-adjacent phrasing ('research' is handled by word boundaries, but a bare
# 'search for wins' should not scope the report to google_search either).
_LENS_ALIAS_SKIP = {"search", "video", "shopping", "performance", "display", "social"}


def _detect_channels(text: str, known, aliases) -> list | None:
    found = set()
    for ch in known:
        if phrase_in(text, ch):
            found.add(ch)
    for alias, canon in (aliases or {}).items():
        if len(alias) < 3 or alias in _LENS_ALIAS_SKIP:
            continue
        if canon in known and phrase_in(text, alias):
            found.add(canon)
    return sorted(found) or None


def select_metrics(primary_tier: str, registry) -> list:
    """Primary-tier metric keys first, then one headline metric from each other tier."""
    by_tier = {}
    for spec in registry:
        by_tier.setdefault(spec["tier"], []).append(spec["key"])
    ordered = list(by_tier.get(primary_tier, []))
    for tier in M.TIERS:
        if tier != primary_tier and by_tier.get(tier):
            ordered.append(by_tier[tier][0])
    return ordered


def _validate(spec: ReportSpec, goals, registry) -> ReportSpec:
    valid_goals = set((goals.get("goal_primary_tier") or {}).keys())
    if valid_goals and spec.goal not in valid_goals:
        spec.goal = goals.get("default_goal", "conversion")
        spec.primary_tier = M.primary_tier(spec.goal, goals)
    if spec.primary_tier not in set(M.TIERS):
        spec.primary_tier = "outcome"
    keys = {m["key"] for m in registry}
    spec.metrics = [k for k in spec.metrics if k in keys] or sorted(keys)
    return spec


def _parse_with_llm(text, goals, registry, config, mappings):
    """Optional LLM parse -> ReportSpec. Returns None on any failure (fall back to rules)."""
    try:
        import anthropic
        valid_goals = list((goals.get("goal_primary_tier") or {}).keys())
        known = list((config.get("modeling") or {}).get("channel_spend_cols", []))
        prompt = (
            "Map this marketing-report request to JSON with keys "
            '"goal", "channels", "tone". '
            "goal must be one of " + ", ".join(valid_goals) + ". "
            "channels is a subset of " + ", ".join(known) + " (or []). "
            'tone is one of "standard", "executive", "detailed". '
            "Return ONLY the JSON.\n\nRequest: " + text
        )
        client = anthropic.Anthropic()
        msg = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=200,
                                     messages=[{"role": "user", "content": prompt}])
        raw = msg.content[0].text
        data = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        goal = data.get("goal") if data.get("goal") in valid_goals else M.resolve_goal(text, goals)
        tier = M.primary_tier(goal, goals)
        channels = [c for c in (data.get("channels") or []) if c in known] or None
        tone = data.get("tone") if data.get("tone") in ("standard", "executive", "detailed") else "standard"
        return ReportSpec(goal=goal, primary_tier=tier, metrics=select_metrics(tier, registry),
                          channels=channels, tone=tone, source_text=text)
    except Exception:
        return None


def parse_lens(text, *, goals=None, registry=None, config=None, mappings=None,
               use_llm=None) -> ReportSpec:
    """Parse free text into a ReportSpec. Deterministic by default; LLM if a key is set."""
    goals = M.load_campaign_goals() if goals is None else goals
    registry = M.load_metric_registry() if registry is None else registry
    config = load_config() if config is None else config
    mappings = load_mappings() if mappings is None else mappings
    if use_llm is None:
        use_llm = bool(os.getenv("ANTHROPIC_API_KEY"))

    if use_llm:
        spec = _parse_with_llm(text, goals, registry, config, mappings)
        if spec is not None:
            return _validate(spec, goals, registry)

    goal = M.resolve_goal(text, goals)
    tier = M.primary_tier(goal, goals)
    known = list((config.get("modeling") or {}).get("channel_spend_cols", []))
    channels = _detect_channels(text, known, mappings.get("channel_aliases", {}))
    spec = ReportSpec(goal=goal, primary_tier=tier, metrics=select_metrics(tier, registry),
                      channels=channels, tone=_detect_tone(text), source_text=text)
    return _validate(spec, goals, registry)


def render_narrative(spec: ReportSpec, weekly: pd.DataFrame, *, registry=None) -> str:
    """Goal-tailored, uncertainty-aware narrative. Every figure is computed, never invented."""
    registry = M.load_metric_registry() if registry is None else registry
    wk = weekly if spec.channels is None else weekly[weekly["channel"].isin(spec.channels)]
    long = M.compute_metrics(wk, by=None, registry=registry).set_index("metric")

    scope = ", ".join(spec.channels) if spec.channels else "all channels"
    L = ["# Report — " + spec.goal + " lens\n"]
    L.append("_Goal: **" + spec.goal + "** -> primary tier: **" + spec.primary_tier +
             "**. Scope: " + scope + ". Tone: " + spec.tone + "._\n")

    L.append("## Headline - " + spec.primary_tier + "\n")
    prim = long[long["tier"] == spec.primary_tier]
    for _, r in prim.iterrows():
        L.append("- **" + str(r["label"]) + "**: " + M.format_value(r["value"], r["format"]))

    others = [k for k in spec.metrics
              if k in long.index and long.loc[k, "tier"] != spec.primary_tier]
    if others and spec.tone != "executive":
        L.append("\n## Also")
        for k in others:
            r = long.loc[k]
            L.append("- " + str(r["label"]) + ": " + M.format_value(r["value"], r["format"]))

    if spec.tone == "detailed":
        L.append("\n## Funnel")
        for r in M.funnel(wk).to_dict("records"):
            rate = "-" if pd.isna(r["step_rate"]) else f"{r['step_rate'] * 100:.1f}%"
            L.append("- " + str(r["label"]) + ": " + M.format_value(r["value"], "count") +
                     " (" + rate + " from prior stage)")

    L.append("\n## Caveats")
    L.append("- These are descriptive performance metrics for the selected lens, not causal "
             "effects. For incrementality, use the MMM and validate with experiments.")
    L.append("- Figures are aggregates over the current data; segment further before acting.")
    return "\n".join(L)


def lens_report(weekly: pd.DataFrame, text: str, *, goals=None, registry=None,
                use_llm=None) -> dict:
    """Parse the lens text and render the report: ``{spec, narrative}``."""
    spec = parse_lens(text, goals=goals, registry=registry, use_llm=use_llm)
    return {"spec": spec, "narrative": render_narrative(spec, weekly, registry=registry)}
