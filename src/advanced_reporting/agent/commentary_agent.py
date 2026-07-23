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
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..dashboard import insights
from ..dashboard.mmm_view import (cost_per_outcome_intervals, is_count_target,
                                  load_mmm, roi_intervals)
from ..llm import call
from ..reporting.framing import resolve_framing
from ..utils import load_config, load_pipeline_stages, project_root, scope_to_sources
from . import guards, knowledge, summaries
from .recommendations import MAX_RECS, REC_TITLES, eligible_recommendations
from .spec_agent import DEFAULT_MODEL, load_active_spec

COMMENTARY_PATH = Path("outputs/commentary_ai.md")
# machine-readable sidecar (same stamp/hash/logic): sections tagged with the insight
# block they belong to, so the dashboard can weave each paragraph under its chart
COMMENTARY_JSON = Path("outputs/commentary_ai.json")

# section tags beyond the block catalog: the scorecard, the MMM read, and a catch-all
EXTRA_SECTION_TAGS = ("scorecard", "incrementality", "general")
PROMPT_PATH = Path("system/prompts/commentary_agent.md")
STAMP = "AI-drafted from computed facts — review before client use"
# Bump when the deterministic numbers the commentary cites can change (e.g. the
# 2026-07-13 partial-week delta fix moved "-12%" → "-3%"). A commentary stamped with an
# older logic version is treated as STALE and hidden with a re-run note — cached LLM
# prose must never contradict the computed blocks it sits beside. (Independent of
# ``data_hash``, which keys the spec: the data is unchanged, only the reporting logic.)
LOGIC_VERSION = "2026-07-13-recruiting-pipeline"

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
                   kpi_label: str, budget: dict | None,
                   stages: pd.DataFrame | None = None) -> dict:
    money, count = insights._money, lambda v: f"{float(v):,.0f}"
    ratio = lambda v: f"{float(v):.1f}x"  # noqa: E731
    facts: dict = {}

    facts["headline_tiles"] = [
        {k: t[k] for k in ("label", "value", "delta", "help") if t.get(k) is not None}
        for t in insights.headline_tiles(weekly, kpi_label)]
    facts["headline_tiles_note"] = (
        "tile VALUES are full-period totals over PAID campaigns; deltas compare the "
        "last 4 weeks to the prior 4. The scorecard's blended figures divide paid "
        "spend by ALL-traffic outcomes — a different denominator; never equate them.")

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
    if b := insights.recruiting_pipeline_insight(stages):
        facts["recruiting_pipeline"] = {
            "title": b["title"], "narrative": b["narrative"],
            "stages": _records(b["stages"], {
                "label": str, "value": count,
                "step_rate": lambda v: f"{float(v) * 100:.0f}%" if v == v else None}),
            "note": ("selection outcomes, right-censored (recent cohorts still in "
                     "flight) — never attribute these gates to media")}
    return facts


def _scorecard_facts(weekly: pd.DataFrame, tier: str, targets: dict,
                     kpi_label: str, config_target_keys: set | None = None) -> dict:
    sc = insights.tier_scorecard(weekly, tier, targets=targets, kpi_label=kpi_label,
                                 config_target_keys=config_target_keys)
    return {
        "tier": sc["label"],
        # provenance is explicit so prose can't call an industry benchmark a "client
        # target": "client target" / "industry benchmark" / "channel spread".
        "gauges": [{"metric": r["label"], "value": r["value_str"],
                    "verdict": r["verdict"], "bands": r["provenance"]}
                   for r in sc["rag"]],
        "pacing": [{"metric": p["label"], "value": p["value_str"], "note": p["note"]}
                   for p in sc["pace"]],
        "totals": [{"metric": k, "value": v} for k, v in sc["grid"]],
        "targets_in_force": targets or "none configured",
    }


def _mmm_facts(mmm: dict | None) -> dict | None:
    if not mmm:
        return None
    meta = mmm.get("meta") or {}
    money = insights._money
    if is_count_target(meta):
        # Count target: ROI (outcomes/$) is meaningless prose — the facts the agent
        # may cite are COST PER INCREMENTAL OUTCOME intervals vs the client band.
        import numpy as np
        cpo = cost_per_outcome_intervals(mmm["summary"], meta)
        good, warn = float(cpo["good"].iloc[0]), float(cpo["warn"].iloc[0])
        rows = []
        for _, r in cpo.iterrows():
            finite = np.isfinite(float(r["cost_high"]))
            rows.append({
                "channel": str(r["channel"]),
                "cost_per_incremental": (money(float(r["cost_per"]))
                                         if np.isfinite(float(r["cost_per"]))
                                         else "not measurable"),
                "interval_90": (f"{money(float(r['cost_low']))}-"
                                f"{money(float(r['cost_high']))}" if finite
                                else "cannot rule out zero incremental effect"),
                "verdict": str(r["verdict"])})
        return {"note": "modeled estimates with 90% intervals — directional, hedge "
                        "causal language. Cost per INCREMENTAL outcome (the MMM "
                        "target), graded against the client band — a different "
                        "denominator from the descriptive cost-per figures above; "
                        "never blend the two.",
                "client_band": {"good": money(good), "warn": money(warn),
                                "provenance": "client target"},
                "cost_per_incremental_by_channel": rows}
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
    # framing resolver: stamped AI commentary must not inherit a stale engagement's
    # label/targets/funnel (the CLIENTXYZ finding). Neutralized silently — the FACTS
    # are computed from the resolved framing; the dashboard/report surface status.
    stages = load_pipeline_stages(cfg, root)
    res = resolve_framing(weekly, root, cfg=cfg, spec=spec, stages=stages)
    kpi_label = res.kpi_label
    targets = res.targets
    stages = res.stages
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
        "insights": _insight_facts(weekly, hist, kpi_label, res.budget,
                                   stages=stages),
        "primary_tier_scorecard": _scorecard_facts(
            weekly, tier, targets, kpi_label,
            config_target_keys=set(res.client_target_keys)),
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
    from .validate import BLOCK_CATALOG
    props: dict = {
        "lede": {"type": "string"},
        "sections": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["title", "text", "block"],
            "properties": {"title": {"type": "string"},
                           "text": {"type": "string"},
                           "block": {"enum": list(BLOCK_CATALOG)
                                     + list(EXTRA_SECTION_TAGS)}}}},
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


_UESC = re.compile(r"\\u([0-9a-fA-F]{4})")


def _normalize_text(s: str) -> str:
    """Decode literal ``\\uXXXX`` sequences the model sometimes writes INSIDE its JSON
    strings (double-escaped em dashes rendered as raw ``\\u2014`` on the dashboard —
    live finding 2026-07-13)."""
    return _UESC.sub(lambda m: chr(int(m.group(1), 16)), str(s))


def _render_body(data: dict, eligible: list[dict]) -> tuple[str, list[str]]:
    """Deterministic markdown rendering of the structured reply. Returns
    ``(body, dropped)`` — recommendations whose type somehow isn't eligible are
    dropped and recorded (belt over the schema's braces)."""
    eligible_types = {r["type"] for r in eligible}
    parts = [_normalize_text(data.get("lede", "")).strip()]
    sections = list(data.get("sections") or [])
    # a recap section under any name duplicates the lede AND crowds a real section
    # past the cap (the model has tried "Overview", then "Headline performance")
    _recap = re.compile(r"overview|summary|headline|at a glance", re.IGNORECASE)
    recap_dropped = [s for s in sections if _recap.search(str(s.get("title", "")))]
    sections = [s for s in sections if s not in recap_dropped]
    # cap = one section per catalog block + scorecard + MMM (the old 6 silently
    # dropped the recruiting-pipeline section once the catalog grew to 6 blocks)
    _max_sections = 8
    if len(sections) > _max_sections:
        dropped_sections = len(sections) - _max_sections
        sections = sections[:_max_sections]
    else:
        dropped_sections = 0
    clean_sections = [{"block": s.get("block") or "general",
                       "title": _normalize_text(s.get("title", "")).strip(),
                       "text": _normalize_text(s.get("text", "")).strip()}
                      for s in sections]
    for s in clean_sections:
        parts.append(f"## {s['title']}\n\n{s['text']}")
    dropped: list[str] = []
    if recap_dropped:
        dropped.append(f"{len(recap_dropped)} recap section(s) (duplicate of the lede)")
    if dropped_sections:
        dropped.append(f"{dropped_sections} section(s) over the {_max_sections}-section cap")
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
        # plain-English title, never the raw enum ("cut_or_restructure" on a client
        # screen reads as internal jargon)
        title = REC_TITLES.get(r["type"], r["type"].replace("_", " ").capitalize())
        recs.append(f"- **{title}** _({grade})_ — "
                    f"{_normalize_text(r.get('text', '')).strip()}")
    if recs:
        parts.append("## Recommendations\n\n"
                     "Ordered by money at stake; max "
                     f"{MAX_RECS} per report (recommendation_menu.md).\n\n"
                     + "\n".join(recs))
    structured = {"lede": parts[0], "sections": clean_sections,
                  "recommendations_md": "\n".join(recs)}
    return "\n\n".join(p for p in parts if p), dropped, structured


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

    context_block = knowledge.as_block(knowledge.load_context(root))
    prompt = (template_f.read_text(encoding="utf-8")
              .replace("{guidelines}", knowledge.as_block(knowledge.load_guidelines(root)))
              .replace("{context}", context_block)
              .replace("{facts}", json.dumps(facts, indent=1, default=str))
              .replace("{eligible_recommendations}",
                       json.dumps(eligible, indent=1, default=str) if eligible
                       else "(none — the computed data made no recommendation "
                            "eligible; write none)")
              .replace("{max_recs}", str(MAX_RECS)))

    # The guard runs on the AGENT-AUTHORED text only (lede, section texts, rec
    # texts) — the deterministic renderer's boilerplate is ours, not the model's.
    # Allowed sources for a numeral: the computed facts, the eligible recs'
    # computed evidence, and the CLIENT CONTEXT (Harry-authored, per-engagement —
    # a band written in the brief is curated truth, not a fabrication). The
    # general guidelines are deliberately NOT allowed: playbook benchmarks must
    # arrive via the spec's validated targets, or a stale education CPC could
    # leak into a recruitment report (live finding 2026-07-11).
    #
    # One guarded RETRY on rejection, feeding the violations back (live finding:
    # a draft garbled 2,255 into 2254 — a stochastic digit slip; one corrected
    # attempt usually clears it). A second failure stays loud and unpublished.
    allowed = {"facts": facts, "eligible": eligible,
               "client_context": context_block}
    data = body = None
    violations: list[str] = []
    info: dict = {}
    for attempt in (1, 2):
        retry_note = "" if attempt == 1 else (
            "\n\nYour previous draft was REJECTED by the number guard:\n- "
            + "\n- ".join(violations)
            + "\nEvery numeral must appear in FACTS / ELIGIBLE RECOMMENDATIONS / "
              "CLIENT CONTEXT exactly as given. Redraft, correcting or removing "
              "the offending numbers.")
        # 16k: the FBI-scale FACTS payload (6 insight blocks incl. the recruiting
        # pipeline) pushed replies past the old 8k ceiling (truncated 2026-07-13)
        data, info = call(prompt + retry_note, model=model,
                          schema=_schema(eligible), max_tokens=16000)
        if data is None:
            return None, info
        authored = "\n".join(
            [str(data.get("lede", ""))]
            + [f"{s.get('title', '')}\n{s.get('text', '')}"
               for s in data.get("sections") or []]
            + [str(r.get("text", "")) for r in data.get("recommendations") or []])
        violations = guards.check_output(authored, allowed)
        if not violations:
            break
        info["retried"] = attempt == 1
    body, dropped, structured = _render_body(data, eligible)
    info["dropped"] = dropped
    if violations:
        info["violations"] = violations
        info["error"] = f"REJECTED by the number guard ({len(violations)} violations)"
        return None, info

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data_hash = summaries.data_hash(root)
    front = "\n".join([
        "---", f"stamp: {STAMP}",
        f"generated_at: {generated_at}",
        f"model: {info.get('model')}",
        f"data_hash: {data_hash}",
        f"logic_version: {LOGIC_VERSION}", "---", "",
    ])
    out_f = root / COMMENTARY_PATH
    out_f.parent.mkdir(parents=True, exist_ok=True)
    out_f.write_text(front + body + "\n", encoding="utf-8")
    # the sidecar carries the SAME guard-passed text, tagged by block, so the
    # dashboard can weave each paragraph under its chart
    (root / COMMENTARY_JSON).write_text(json.dumps({
        "stamp": STAMP, "generated_at": generated_at, "model": info.get("model"),
        "data_hash": data_hash, "logic_version": LOGIC_VERSION, **structured,
    }, indent=1), encoding="utf-8")
    return body, info


def load_active_commentary_sections(root: Path | None = None) -> tuple[dict | None, str | None]:
    """The dashboard's WEAVE read path: the block-tagged sidecar, or ``(None, note)``.

    Same staleness rules as the markdown artifact (data hash + logic version) — a
    woven paragraph must never contradict the chart it sits under. Missing sidecar
    (pre-weave artifact) -> ``(None, None)``: pages fall back to the standalone md.
    """
    root = root or project_root()
    f = root / COMMENTARY_JSON
    if not f.exists():
        return None, None
    try:
        payload = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, (f"{COMMENTARY_JSON} unreadable — re-run "
                      "scripts/advise.py --commentary")
    if payload.get("data_hash") != str(summaries.data_hash(root)):
        return None, "AI commentary is stale (data changed) — re-run advise.py --commentary"
    if payload.get("logic_version") != LOGIC_VERSION:
        return None, ("AI commentary is stale (report logic changed) — re-run "
                      "advise.py --commentary")
    return payload, None


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
    stamped, logic = None, None
    for line in front.splitlines():
        if line.startswith("data_hash:"):
            stamped = line.split(":", 1)[1].strip()
        elif line.startswith("logic_version:"):
            logic = line.split(":", 1)[1].strip()
    if stamped != str(summaries.data_hash(root)):
        return None, ("AI commentary is stale (data changed since it was drafted) — "
                      "hidden; re-run scripts/advise.py --commentary")
    if logic != LOGIC_VERSION:
        return None, ("AI commentary is stale (report logic changed since it was drafted "
                      "— its figures may not match the computed blocks) — hidden; re-run "
                      "scripts/advise.py --commentary")
    return body, None
