"""A1 — the report-spec agent. One guarded structured-output call at PIPELINE time
(never per page load) that turns guidelines + client context + compact computed
summaries into ``outputs/report_spec.json``.

The dashboard treats the spec exactly like config: spec fills the gaps config
leaves, explicit config keys always win, and no key / no spec means current
behavior unchanged. The spec is cached per data-hash — stale (data changed) means
ignored, with a visible note, until ``scripts/advise.py --spec`` is re-run.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..llm import call
from ..reporting import metrics as M
from ..utils import load_config, project_root
from . import knowledge, summaries
from .validate import BLOCK_CATALOG, CAMPAIGN_TYPES, MAX_WATCH_FLAGS, validate_spec

SPEC_PATH = Path("outputs/report_spec.json")
PROMPT_PATH = Path("system/prompts/spec_agent.md")
DEFAULT_MODEL = "claude-sonnet-5"


def _schema(registry: list[dict]) -> dict:
    """The structured-output contract. Enums pin every closed vocabulary at the API
    layer; validate.py re-checks locally anyway (clip, never trust)."""
    metric_keys = [m["key"] for m in registry]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["campaign_type", "primary_tier", "kpi_label", "targets",
                     "blocks", "watch_flags", "rationale"],
        "properties": {
            "campaign_type": {"enum": list(CAMPAIGN_TYPES)},
            "primary_tier": {"enum": list(M.TIERS)},
            "kpi_label": {"type": "string", "maxLength": 60},
            "targets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["metric"],
                    "properties": {
                        "metric": {"enum": metric_keys},
                        "goal": {"type": "number"},
                        "good": {"type": "number"},
                        "warn": {"type": "number"},
                    },
                },
            },
            "blocks": {"type": "array", "items": {"enum": list(BLOCK_CATALOG)}},
            "watch_flags": {"type": "array", "maxItems": MAX_WATCH_FLAGS,
                            "items": {"type": "string", "maxLength": 300}},
            "rationale": {"type": "string", "maxLength": 600},
        },
    }


def build_prompt(root: Path | None = None) -> str:
    """Fill the versioned template (system/prompts/spec_agent.md). Token replacement,
    not str.format — the template legitimately contains literal braces."""
    root = root or project_root()
    template_f = root / PROMPT_PATH
    if not template_f.exists():
        raise FileNotFoundError(
            f"{template_f} missing — the spec-agent prompt template is versioned "
            "knowledge; restore it from git before running the agent.")
    summary = summaries.summary_block(root)
    if summary is None:
        raise FileNotFoundError(
            "no processed data (data/processed/channel_weekly_metrics.csv) — run "
            "scripts/run_pipeline.py before scripts/advise.py --spec.")
    return (template_f.read_text(encoding="utf-8")
            .replace("{guidelines}", knowledge.as_block(knowledge.load_guidelines(root)))
            .replace("{context}", knowledge.as_block(knowledge.load_context(root)))
            .replace("{data_summary}", summary)
            .replace("{catalog}", ", ".join(BLOCK_CATALOG)))


def generate_spec(root: Path | None = None, model: str | None = None):
    """Run the spec agent and write ``outputs/report_spec.json``.

    Returns ``(spec, info)``; ``spec`` is None on any failure (no key, no data,
    agent disabled, call failed) with the reason in ``info['error']`` — callers
    print it and fall back to deterministic defaults, per the house pattern.
    """
    root = root or project_root()
    agent_cfg = (load_config().get("agent") or {})
    if agent_cfg.get("enabled", True) is False:
        return None, {"error": "agent.enabled is false in config (per-engagement "
                               "data-egress opt-out)"}
    model = model or agent_cfg.get("model") or DEFAULT_MODEL

    try:
        prompt = build_prompt(root)
    except FileNotFoundError as e:
        return None, {"error": str(e)}
    registry = M.load_metric_registry()
    raw, info = call(prompt, model=model, schema=_schema(registry), max_tokens=2000)
    if raw is None:
        return None, info

    spec, dropped = validate_spec(raw, registry)
    info["dropped"] = dropped
    payload = {
        "spec": spec,
        "meta": {
            "data_hash": summaries.data_hash(root),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": info.get("model"),
            "cost_usd": info.get("cost_usd"),
            "input_tokens": info.get("input_tokens"),
            "output_tokens": info.get("output_tokens"),
            "dropped": dropped,
        },
    }
    out_f = root / SPEC_PATH
    out_f.parent.mkdir(parents=True, exist_ok=True)
    out_f.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return spec, info


def load_active_spec(root: Path | None = None) -> tuple[dict, str | None]:
    """The dashboard's read path. Returns ``(spec, note)``:

    - no spec file -> ``({}, None)`` — silence, current behavior;
    - hash mismatch (data changed since generation) -> ``({}, <visible note>)``;
    - unreadable file -> ``({}, <visible note>)``;
    - current -> ``(spec, None)``.
    """
    root = root or project_root()
    f = root / SPEC_PATH
    if not f.exists():
        return {}, None
    try:
        payload = json.loads(f.read_text(encoding="utf-8"))
        spec = payload.get("spec") or {}
        stamped = (payload.get("meta") or {}).get("data_hash")
    except (json.JSONDecodeError, AttributeError):
        return {}, f"{SPEC_PATH} is unreadable — ignored; re-run scripts/advise.py --spec"
    if stamped != summaries.data_hash(root):
        return {}, ("report spec is stale (data changed since it was generated) — "
                    "ignored; re-run scripts/advise.py --spec")
    # re-validate on read: the file is user-editable, and the vocab may have moved
    spec, _ = validate_spec(spec)
    return spec, None
