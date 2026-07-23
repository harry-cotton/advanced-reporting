"""Intake: deterministic facts + framing proposal for the Setup page.

The brief's invariant (docs/design-intake-agent.md): **the form can only offer what
exists in the loaded data** — every selectable option here is a column with usable
rows. Agents CONFIGURE, the engine COMPUTES: the proposal is pure pandas over the
already-computed ``summaries.data_summary()``; the optional LLM narration is
display-only (values are computed before the call and never changed by it — the
``lens.py`` guarded pattern). No key -> plainer wording, full function.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..reporting import metrics as M
from ..reporting.framing import (PLUMBED_KPI_METRICS, ResolvedFraming,
                                 intake_status, resolve_framing)  # noqa: F401  (re-export)
from ..utils import load_config, project_root
from . import summaries

# Funnel steps the form may offer, in tier order (reach -> intent -> outcome).
# Explicit rather than derived: registry tiers describe METRICS, not base columns.
FUNNEL_CANDIDATES = ("impressions", "clicks", "sessions", "engaged_sessions",
                     "page_views", "video_views", "conversions", "key_events")

# Registry key of the cost-per-primary-outcome metric, per plumbed outcome.
COST_PER_KEY = {"key_events": "cost_per_key_event", "conversions": "cpa"}


def outcome_coverage(weekly: pd.DataFrame) -> dict:
    """Per plumbed outcome column: presence, provenance kind, and week coverage."""
    total = int(weekly["date"].nunique()) if len(weekly) else 0
    out = {}
    for col, kind in (("key_events", "measured"),
                      ("conversions", "platform-claimed")):
        present = col in weekly.columns and weekly[col].notna().any()
        weeks = (int(weekly.loc[weekly[col].notna(), "date"].nunique())
                 if present else 0)
        out[col] = {"present": present, "kind": kind,
                    "weeks_with_data": weeks, "weeks_total": total}
    return out


def funnel_candidates(weekly: pd.DataFrame) -> list[str]:
    """Backed base columns in tier order — the only steps the form offers."""
    return [c for c in FUNNEL_CANDIDATES
            if c in weekly.columns and weekly[c].notna().any()]


def intake_facts(root: Path | None = None, weekly: pd.DataFrame | None = None,
                 resolved: ResolvedFraming | None = None) -> dict:
    """Zone A of the Setup page: read-only facts about the LOADED data.

    Reuses ``summaries.data_summary()`` (never recomputes what the pipeline already
    computed) plus the intake-specific coverage/candidate views. ``resolved`` (the
    page's own resolve) supplies the configured-but-absent callouts."""
    root = root or project_root()
    facts: dict = {"summary": summaries.data_summary(root)}
    if weekly is not None and len(weekly):
        facts["outcome_coverage"] = outcome_coverage(weekly)
        facts["funnel_candidates"] = funnel_candidates(weekly)
    if resolved is not None:
        facts["status"] = resolved.status
        facts["mismatches"] = [str(m) for m in resolved.mismatches]
    facts["currency"] = ((load_config().get("project") or {})
                         .get("currency", "USD"))
    return facts


def propose_framing(weekly: pd.DataFrame, facts: dict | None = None) -> dict:
    """The deterministic proposal: measured beats platform-claimed, funnel = the
    backed candidates in tier order. No target or naming proposals — business
    judgments are asked, never guessed."""
    cov = (facts or {}).get("outcome_coverage") or outcome_coverage(weekly)
    metric = "key_events" if cov["key_events"]["present"] else "conversions"
    reg = {m["key"]: m for m in M.load_metric_registry()}
    label = str((reg.get(metric) or {}).get("label", metric.replace("_", " ")))
    return {"kpi_metric": metric, "kpi_label": label.lower(),
            "funnel_steps": funnel_candidates(weekly)}


def proposal_sentence(proposal: dict, cov: dict) -> str:
    """The no-key wording — plain, complete, and exactly what the LLM would only
    rephrase."""
    m = proposal["kpi_metric"]
    c = cov.get(m) or {}
    bits = [f"**Suggested primary outcome: `{m}`** — {c.get('kind', '?')}, data in "
            f"{c.get('weeks_with_data', 0)}/{c.get('weeks_total', 0)} weeks."]
    alt = "conversions" if m == "key_events" else "key_events"
    if (cov.get(alt) or {}).get("present"):
        bits.append(f"Alternative: `{alt}` ({cov[alt]['kind']}).")
    if proposal.get("funnel_steps"):
        bits.append("Suggested funnel: "
                    + " → ".join(proposal["funnel_steps"])
                    + ", from the columns present.")
    return " ".join(bits)


def narrate_proposal(proposal: dict, facts: dict) -> tuple[str | None, str | None]:
    """Optional LLM narration -> ``(narrative, kpi_label_suggestion)``.

    Display-only: the proposed VALUES are already computed; the model may only
    explain them and suggest a friendlier label prefill. No key / any failure ->
    ``(None, None)`` and the caller renders ``proposal_sentence`` instead."""
    from .. import llm
    if not llm.llm_enabled():
        return None, None
    cfg = load_config()
    model = ((cfg.get("agent") or {}).get("model")) or "claude-sonnet-5"
    schema = {"type": "object",
              "properties": {"narrative": {"type": "string"},
                             "kpi_label_suggestion": {"type": "string"}},
              "required": ["narrative"], "additionalProperties": False}
    compact = {k: facts.get(k) for k in ("outcome_coverage", "funnel_candidates",
                                         "currency", "mismatches")}
    prompt = (
        "You are narrating an already-decided intake proposal for a marketing "
        "reporting tool. Explain in 2-3 plain sentences WHY this proposal fits "
        "the data facts (measured beats platform-claimed; coverage). You may "
        "suggest a friendlier client-facing kpi_label. Do NOT change, add, or "
        "second-guess any proposed value.\n\n"
        f"Proposal: {proposal}\n\nData facts: {compact}")
    data, _info = llm.call(prompt, model=model, schema=schema, max_tokens=400)
    if not isinstance(data, dict) or not data.get("narrative"):
        return None, None
    return str(data["narrative"]), (str(data["kpi_label_suggestion"]).strip()
                                    if data.get("kpi_label_suggestion") else None)
