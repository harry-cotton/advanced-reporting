"""Spec validation — planner-rails style: clip, never trust.

Every field the model proposes is checked against a vocabulary that exists (the
metric registry, the block catalog, the tier/type enums). Invalid values are
DROPPED and the drop is recorded so the caller can print it loudly; the dashboard's
deterministic defaults fill anything missing. Nothing here ever raises on bad model
output — a useless spec degrades to "no spec", never to a broken report.
"""
from __future__ import annotations

from ..reporting import metrics as M

CAMPAIGN_TYPES = ("awareness", "engagement", "conversion")

# The fixed insight-block catalog: the Overview's deterministic renderers, by name,
# in their default order. The spec may select and reorder; it can never add.
BLOCK_CATALOG = ("kpi_trend", "claims_vs_measured", "cost_per_outcome",
                 "audience_callout", "pacing")

MAX_WATCH_FLAGS = 3
_MAX_LABEL = 60
_MAX_FLAG = 300
_MAX_RATIONALE = 600


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None      # NaN -> None


def _clean_targets(raw, registry_keys: set[str], dropped: list[str]) -> dict:
    """Accepts the schema's list-of-{metric, goal, good, warn} (or a dict for
    robustness); returns the config-shaped ``{metric: {goal/good/warn}}``."""
    if isinstance(raw, dict):
        raw = [{"metric": k, **(v or {})} for k, v in raw.items()]
    out: dict = {}
    for entry in raw or []:
        if not isinstance(entry, dict):
            dropped.append(f"targets: non-object entry {entry!r}")
            continue
        key = entry.get("metric")
        if key not in registry_keys:
            dropped.append(f"targets: unknown metric key {key!r}")
            continue
        bands = {k: _num(entry.get(k)) for k in ("goal", "good", "warn")}
        bands = {k: v for k, v in bands.items() if v is not None}
        if not bands:
            dropped.append(f"targets: {key} has no usable goal/good/warn values")
            continue
        out[key] = bands
    return out


def validate_spec(raw: dict, registry: list[dict] | None = None) -> tuple[dict, list[str]]:
    """Clip a raw model spec to the vocab. Returns ``(spec, dropped)`` where
    ``dropped`` lists every discarded field with its reason (print it — the brief's
    "invalid fields fall back to defaults, loudly")."""
    dropped: list[str] = []
    spec: dict = {}
    if not isinstance(raw, dict):
        return {}, [f"spec: expected an object, got {type(raw).__name__}"]

    ct = raw.get("campaign_type")
    if ct in CAMPAIGN_TYPES:
        spec["campaign_type"] = ct
    elif ct is not None:
        dropped.append(f"campaign_type: {ct!r} not in {CAMPAIGN_TYPES}")

    tier = raw.get("primary_tier")
    if tier in M.TIERS:
        spec["primary_tier"] = tier
    elif tier is not None:
        dropped.append(f"primary_tier: {tier!r} not in {M.TIERS}")

    label = raw.get("kpi_label")
    if isinstance(label, str) and label.strip():
        spec["kpi_label"] = label.strip()[:_MAX_LABEL]
    elif label is not None:
        dropped.append(f"kpi_label: unusable value {label!r}")

    registry = registry if registry is not None else M.load_metric_registry()
    targets = _clean_targets(raw.get("targets"), {m["key"] for m in registry}, dropped)
    if targets:
        spec["targets"] = targets

    blocks, seen = [], set()
    for b in raw.get("blocks") or []:
        if b in BLOCK_CATALOG and b not in seen:
            blocks.append(b)
            seen.add(b)
        else:
            dropped.append(f"blocks: {b!r} not in catalog (or duplicate)")
    if blocks:
        spec["blocks"] = blocks

    flags = [str(f).strip()[:_MAX_FLAG] for f in (raw.get("watch_flags") or [])
             if str(f).strip()]
    if len(flags) > MAX_WATCH_FLAGS:
        dropped.append(f"watch_flags: {len(flags)} given, keeping first {MAX_WATCH_FLAGS}")
        flags = flags[:MAX_WATCH_FLAGS]
    if flags:
        spec["watch_flags"] = flags

    rationale = raw.get("rationale")
    if isinstance(rationale, str) and rationale.strip():
        spec["rationale"] = rationale.strip()[:_MAX_RATIONALE]
    return spec, dropped
