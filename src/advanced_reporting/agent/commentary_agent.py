"""A2 — the commentary & recommendations agent. Second guarded call, also offline,
writing ``outputs/commentary_ai.md``.

Prose may only restate computed facts: the FACTS payload is built from the same
``insights.py`` dicts the dashboard renders, plus the tier scorecard, MMM summary
when present, and the deterministically-computed ELIGIBLE recommendations
(``recommendations.py``). The model's structured output pins recommendation types
to the eligible set at the API layer; ``guards.check_output`` then rejects the
whole artifact if any numeral (or multiplier word) isn't backed by FACTS.
The artifact carries a front-matter stamp and the dashboard shows it only when
``reporting.ai_commentary: true`` — off by default.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..dashboard import insights
from ..dashboard.mmm_view import load_mmm, roi_intervals
from ..llm import call
from ..utils import load_config, project_root, scope_to_sources
from . import guards, knowledge, summaries
from .recommendations import MAX_RECS, eligible_recommendations
from .spec_agent import DEFAULT_MODEL, load_active_spec

COMMENTARY_PATH = Path("outputs/commentary_ai.md")
PROMPT_PATH = Path("system/prompts/commentary_agent.md")
STAMP = "AI-drafted from computed facts — review before client use"

_GRADES = ("platform-claimed", "analytics-measured", "modeled")


# ------------------------------------------------------------------ facts payload
def _records(df: pd.DataFrame, formats: dict) -> list[dict]:
    """Compact records with values formatted the way prose should cite them."""
    out = []
    for _, row in df.iterrows():
        rec = {}
        for col, fmt in formats.items():
            if col not in df.columns:
                continue
            v = row[col]
            rec[col] = fmt(v) if callable(fmt) else v
        out.append(rec)
    return out


def _insight_facts(weekly: pd.DataFrame, hist: pd.DataFrame | None,
                   kpi_label: str, budget: dict | None) -> dict:
    money, count = insights._money, lambda v: f"{float(v):,.0f}"
    ratio = lambda v: f"{float(v):.1f}x"  # noqa: E731
    facts: dict = {}

    facts["headline_tiles"] = [
        {k: t[k] for k in ("label", "value", "delta") if t.get(k) is not None}
        for t in insights.headline_tiles(weekly, kpi_label)]

    if b := insights.kpi_trend_insight(weekly, kpi_label):
        facts["kpi_trend"] = {"title": b["title"], "narrative": b["narrative"]}
    if b := insights.claims_vs_measured_insight(weekly, kpi_label):
        facts["claims_vs_measured"] = {
            "title": b["title"], "narrative": b["narrative"],
            "overall_ratio": ratio(b["overall_ratio"]),
            "per_channel": _records(b["per_channel"], {
                "channel": str, "claimed": count, "measured": count,
                "ratio": ratio})}
    if b := insights.cost_per_outcome_insight(weekly, kpi_label):
        facts["cost_per_outcome"] = {
            "title": b["title"], "narrative": b["narrative"],
            "outcome_label": b["outcome_label"], "measured": b["measured"],
            "per_channel": _records(b["per_channel"], {
                "channel": str, "spend": money, "cost_per": money})}
    if b := insights.pacing_insight(weekly, budget):
        facts["pacing"] = {"title": b["title"], "narrative": b["narrative"],
                           "total_spend": money(b["total_spend"]),
                           "run_rate_per_week": money(b["run_rate"]),
                           "n_weeks": b["n_weeks"]}
    if hist is not None:
        if b := insights.audience_callout_insight(hist):
            facts["audience_callout"] = {
                "title": b["title"], "narrative": b["narrative"],
                "gap": ratio(b["mult"]),
                "note": "all audience figures platform-claimed"}
    return facts


def _scorecard_facts(weekly: pd.DataFrame, tier: str, targets: dict,
                     kpi_label: str) -> dict:
    sc = insights.tier_scorecard(weekly, tier, targets=targets, kpi_label=kpi_label)
    return {
        "tier": sc["label"],
        "gauges": [{"metric": r["label"], "value": r["value_str"],
                    "verdict": r["verdict"],
                    "bands": ("configured targets" if r["mode"] == "absolute"
                              else "channel spread (no absolute target set)")}
                   for r in sc["rag"]],
        "pacing": [{"metric": p["label"], "value": p["value_str"], "note": p["note"]}
                   for p in sc["pace"]],
        "totals": [{"metric": k, "value": v} for k, v in sc["grid"]],
        "targets_in_force": targets or "none configured",
    }


def _mmm_facts(mmm: dict | None) -> dict | None:
    if not mmm:
        return None
    iv = roi_intervals(mmm["summary"])
    return {"note": "modeled estimates with 90% intervals — directional, hedge "
                    "causal language",
            "roi_by_channel": [
                {"channel": str(r["channel"]), "roi": f"{r['roi']:.2f}",
                 "interval_90": f"{r['roi_low']:.2f}-{r['roi_high']:.2f}",
                 "verdict": r["verdict"]}
                for _, r in iv.iterrows()]}


def build_facts(root: Path | None = None) -> tuple[dict, list[dict]] | None:
    """(facts, eligible_recommendations), or None when the pipeline hasn't run."""
    root = root or project_root()
    weekly_f = root / summaries.WEEKLY_CSV
    if not weekly_f.exists():
        return None
    weekly = pd.read_csv(weekly_f, parse_dates=["date"])
    cfg = load_config()
    hist_f = root / summaries.HISTORY_PQ
    # weekly csv is already pipeline-scoped; the raw store is not — scope it here
    hist = (scope_to_sources(pd.read_parquet(hist_f), cfg)
            if hist_f.exists() else None)
    mmm = load_mmm(root / "outputs")

    rep = (cfg.get("reporting") or {})
    spec, _ = load_active_spec(root)
    kpi_label = rep.get("kpi_label") or spec.get("kpi_label") or "key events"
    targets = {**(spec.get("targets") or {}), **(rep.get("targets") or {})}
    tier = spec.get("primary_tier") or (
        "outcome" if insights._has_measured(weekly) else "reach")

    unparsed = None
    if hist is not None:
        try:
            from ..dashboard.drilldown import unparsed_stats
            unparsed = unparsed_stats(hist)
        except Exception:
            pass

    facts = {
        "kpi_label": kpi_label,
        "date_range": [str(weekly["date"].min().date()),
                       str(weekly["date"].max().date())],
        "n_paid_channels": len(insights._paid_channels(weekly)),
        "insights": _insight_facts(weekly, hist, kpi_label, rep.get("budget")),
        "primary_tier_scorecard": _scorecard_facts(weekly, tier, targets, kpi_label),
    }
    if (m := _mmm_facts(mmm)) is not None:
        facts["mmm"] = m
    if unparsed is not None:
        facts["unparsed_names"] = {
            "spend_share": f"{unparsed['spend_rate'] * 100:.0f}%",
            "row_share": f"{unparsed['row_rate'] * 100:.0f}%"}

    recs = eligible_recommendations(weekly, hist=hist, mmm=mmm, unparsed=unparsed)
    return facts, recs


# ------------------------------------------------------------------ the guarded call
def _schema(eligible: list[dict]) -> dict:
    """Structured-output contract. Size constraints (maxItems/maxLength) are NOT in
    the API's schema subset (400) — counts are enforced by the renderer instead.
    When nothing is eligible the ``recommendations`` property is omitted entirely
    (additionalProperties: false makes it unwritable), rather than an empty enum."""
    types = sorted({r["type"] for r in eligible})
    props: dict = {
        "lede": {"type": "string"},
        "sections": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["title", "text"],
            "properties": {"title": {"type": "string"},
                           "text": {"type": "string"}}}},
    }
    required = ["lede", "sections"]
    if types:
        props["recommendations"] = {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["type", "evidence_grade", "text"],
            "properties": {
                "type": {"enum": types},
                "evidence_grade": {"enum": list(_GRADES)},
                "text": {"type": "string"},
            }}}
        required.append("recommendations")
    return {"type": "object", "additionalProperties": False,
            "required": required, "properties": props}


def _render_body(data: dict, eligible: list[dict]) -> tuple[str, list[str]]:
    """Deterministic markdown rendering of the structured reply. Returns
    ``(body, dropped)`` — recommendations whose type somehow isn't eligible are
    dropped and recorded (belt over the schema's braces)."""
    eligible_types = {r["type"] for r in eligible}
    parts = [str(data.get("lede", "")).strip()]
    sections = list(data.get("sections") or [])
    if len(sections) > 6:      # size caps live here now, not in the API schema
        dropped_sections = len(sections) - 6
        sections = sections[:6]
    else:
        dropped_sections = 0
    for s in sections:
        parts.append(f"## {s.get('title', '').strip()}\n\n{s.get('text', '').strip()}")
    dropped: list[str] = []
    if dropped_sections:
        dropped.append(f"{dropped_sections} section(s) over the 6-section cap")
    all_recs = list(data.get("recommendations") or [])
    if len(all_recs) > MAX_RECS:
        dropped.append(f"{len(all_recs) - MAX_RECS} recommendation(s) over the "
                       f"max-{MAX_RECS} cap (recommendation_menu.md)")
        all_recs = all_recs[:MAX_RECS]
    recs = []
    for r in all_recs:
        if r.get("type") not in eligible_types:
            dropped.append(f"recommendation type {r.get('type')!r} not eligible")
            continue
        grade = r.get("evidence_grade")
        grade = grade if grade in _GRADES else "platform-claimed"
        recs.append(f"- **{r['type']}** _({grade})_ — {str(r.get('text', '')).strip()}")
    if recs:
        parts.append("## Recommendations\n\n"
                     "Ordered by money at stake; max "
                     f"{MAX_RECS} per report (recommendation_menu.md).\n\n"
                     + "\n".join(recs))
    return "\n\n".join(p for p in parts if p), dropped


def generate_commentary(root: Path | None = None, model: str | None = None):
    """Run the commentary agent, guard the output, write ``commentary_ai.md``.

    Returns ``(markdown_body, info)``; body is None on any failure — including a
    guard REJECTION, in which case ``info['violations']`` lists every unbacked
    numeral and nothing is written (loud-fail, never published).
    """
    root = root or project_root()
    agent_cfg = (load_config().get("agent") or {})
    if agent_cfg.get("enabled", True) is False:
        return None, {"error": "agent.enabled is false in config (per-engagement "
                               "data-egress opt-out)"}
    model = model or agent_cfg.get("model") or DEFAULT_MODEL

    template_f = root / PROMPT_PATH
    if not template_f.exists():
        return None, {"error": f"{template_f} missing — restore from git"}
    built = build_facts(root)
    if built is None:
        return None, {"error": "no processed data — run scripts/run_pipeline.py "
                               "before scripts/advise.py --commentary"}
    facts, eligible = built

    prompt = (template_f.read_text(encoding="utf-8")
              .replace("{guidelines}", knowledge.as_block(knowledge.load_guidelines(root)))
              .replace("{context}", knowledge.as_block(knowledge.load_context(root)))
              .replace("{facts}", json.dumps(facts, indent=1, default=str))
              .replace("{eligible_recommendations}",
                       json.dumps(eligible, indent=1, default=str) if eligible
                       else "(none — the computed data made no recommendation "
                            "eligible; write none)")
              .replace("{max_recs}", str(MAX_RECS)))

    data, info = call(prompt, model=model, schema=_schema(eligible), max_tokens=8000)
    if data is None:
        return None, info

    # The guard runs on the AGENT-AUTHORED text only (lede, section texts, rec
    # texts) — the deterministic renderer's boilerplate is ours, not the model's.
    # Every numeral must exist in facts OR the eligible recs (their evidence
    # values are computed too, and the agent must cite them).
    authored = "\n".join(
        [str(data.get("lede", ""))]
        + [f"{s.get('title', '')}\n{s.get('text', '')}"
           for s in data.get("sections") or []]
        + [str(r.get("text", "")) for r in data.get("recommendations") or []])
    violations = guards.check_output(authored, {"facts": facts, "eligible": eligible})
    body, dropped = _render_body(data, eligible)
    info["dropped"] = dropped
    if violations:
        info["violations"] = violations
        info["error"] = f"REJECTED by the number guard ({len(violations)} violations)"
        return None, info

    front = "\n".join([
        "---", f"stamp: {STAMP}",
        f"generated_at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"model: {info.get('model')}",
        f"data_hash: {summaries.data_hash(root)}", "---", "",
    ])
    out_f = root / COMMENTARY_PATH
    out_f.parent.mkdir(parents=True, exist_ok=True)
    out_f.write_text(front + body + "\n", encoding="utf-8")
    return body, info


def load_active_commentary(root: Path | None = None) -> tuple[str | None, str | None]:
    """The dashboard's read path: ``(body, note)`` — mirrors the spec's semantics.
    Missing file -> (None, None); stale/unreadable -> (None, visible note)."""
    root = root or project_root()
    f = root / COMMENTARY_PATH
    if not f.exists():
        return None, None
    text = f.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, (f"{COMMENTARY_PATH} has no front-matter stamp — ignored; "
                      "re-run scripts/advise.py --commentary")
    front, body = parts[1], parts[2].strip()
    stamped = None
    for line in front.splitlines():
        if line.startswith("data_hash:"):
            stamped = line.split(":", 1)[1].strip()
    if stamped != str(summaries.data_hash(root)):
        return None, ("AI commentary is stale (data changed since it was drafted) — "
                      "hidden; re-run scripts/advise.py --commentary")
    return body, None
