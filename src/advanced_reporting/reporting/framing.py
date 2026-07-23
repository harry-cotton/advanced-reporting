"""Framing guard: configured report framing must exist in the loaded data.

Discovered 2026-07-23 (docs/notes-intake-agent.md): a stale per-engagement config —
another engagement's KPI label, targets, and applicant-pipeline path — framed a new
client's data, and nothing checked the *configured* framing against what the store
actually holds. This module applies the dashboard's claims-vs-measured honesty
discipline to the tool's OWN configuration: a KPI with zero rows behind it must never
frame a report.

Pure pandas, no model calls, deterministic. Consumers choose the failure mode:

- **Dashboard**: show ``Framing.mismatches`` as a banner (``theme.framing_banner``)
  and render with the neutralized framing the guard returns.
- **Client HTML report / stamped commentary**: call ``require_clean`` — the emailable
  artifact REFUSES to build rather than ship framed around a metric the data cannot
  support.

Out of scope (business judgments no data check can make): client/campaign naming,
budget, and whether the *right* KPI was chosen — that's the intake step's job. The
``data.kpi_path`` business-KPI file is also not checked here (it feeds the MMM
modeling table, not the descriptive framing).
"""
from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from ..dashboard import insights
from ..utils import load_config, project_root
from . import metrics as M

DEFAULT_KPI_LABEL = "key events"

# Durable per-engagement framing, written only by the Setup page (gitignored).
ENGAGEMENT_PATH = Path("config/engagement.yaml")
# v1: insights.py hardcodes the measured/claimed pair (col = key_events if measured
# else conversions), so only these two metric identities are plumbed end-to-end. The
# resolver validates/stores kpi_metric; chart column selection still follows
# _has_measured until insights grows a metric parameter.
PLUMBED_KPI_METRICS = ("key_events", "conversions")
INTAKE_MODES = ("lenient", "strict")
# lenient: guard-passing hand-edited config.yaml framing counts as confirmed (the
# escape hatch, and zero friction for existing installs). strict: only an
# engagement.yaml written by the Setup page confirms. Override: reporting.intake_mode.
DEFAULT_INTAKE_MODE = "lenient"
# Judgment-dependent Overview blocks that must HIDE (not render with guesses) while
# framing is unconfirmed/invalid. Data-fact blocks always render.
HIDDEN_WHEN_UNCONFIRMED = frozenset({"pacing", "recruiting_pipeline"})

_FRAMING_KEYS = ("kpi_metric", "kpi_label", "funnel_steps", "targets",
                 "client_name", "campaign_name", "budget")
_LAYER_PREFIX = {"config": "reporting", "engagement": "engagement",
                 "report_spec": "report_spec"}


@dataclass
class Mismatch:
    """One configured claim the loaded data cannot support."""
    source: str        # where it was configured, e.g. "reporting.kpi_label"
    value: str         # the configured value
    problem: str       # what the data says
    resolution: str    # what the guard did about it

    def __str__(self) -> str:
        return f"{self.source} = {self.value!r}: {self.problem} — {self.resolution}"


@dataclass
class Framing:
    """Guarded framing: what the surfaces should actually use."""
    kpi_label: str
    targets: dict
    stages: pd.DataFrame | None
    mismatches: list[Mismatch] = field(default_factory=list)


class FramingError(RuntimeError):
    """Raised by ``require_clean`` when configured framing contradicts the data."""

    def __init__(self, mismatches: list[Mismatch], artifact: str):
        self.mismatches = mismatches
        lines = "\n".join(f"  - {m}" for m in mismatches)
        super().__init__(
            f"refusing to build {artifact}: the configured framing does not match the "
            f"loaded data (stale engagement config?):\n{lines}\n"
            "Fix config/config.yaml (or clear the stale keys) and rebuild.")


def guard_framing(weekly: pd.DataFrame, *, rep: dict | None = None,
                  spec: dict | None = None,
                  stages: pd.DataFrame | None = None) -> Framing:
    """Check config/spec framing against the loaded weekly table.

    Checks (all deterministic):
    1. A non-default ``kpi_label`` requires an analytics-measured outcome series
       (``key_events`` with any non-NaN value) — else the label names a metric with
       zero rows behind it and is dropped (surfaces fall back to their honest
       platform-claimed labeling).
    2. Every target must grade a metric that actually computes on this data
       (registry metric with a non-NaN, non-zero value) — dead targets are dropped.
    3. The applicant-pipeline ``stages`` frame must overlap the data's date window —
       a disjoint window is another engagement's leftover file; the funnel is dropped.

    Explicit config still wins over the spec everywhere — the guard only removes
    framing the data outright contradicts, it never invents any.
    """
    rep, spec = rep or {}, spec or {}
    mismatches: list[Mismatch] = []

    configured = rep.get("kpi_label") or spec.get("kpi_label")
    kpi_label = configured or DEFAULT_KPI_LABEL
    if (configured and configured.strip().lower() != DEFAULT_KPI_LABEL
            and not insights._has_measured(weekly)):
        source = ("reporting.kpi_label" if rep.get("kpi_label")
                  else "report_spec.kpi_label")
        mismatches.append(Mismatch(
            source, configured,
            "no analytics-measured outcome series (key_events) exists in the loaded "
            "data",
            "label dropped; surfaces fall back to platform-claimed conversions"))
        kpi_label = DEFAULT_KPI_LABEL

    merged = {**(spec.get("targets") or {}), **(rep.get("targets") or {})}
    targets: dict = {}
    if merged:
        try:
            reg = M.load_metric_registry()
            nat = {r["metric"]: r
                   for r in M.compute_metrics(weekly, by=None, registry=reg)
                   .to_dict("records")}
        except Exception:      # a compute failure must not nuke the surface — the
            nat = None         # guard's job is stale config, not metric plumbing
        for key, band in merged.items():
            rec = (nat or {}).get(key)
            if nat is not None and (rec is None or pd.isna(rec["value"])
                                    or float(rec["value"]) == 0.0):
                source = ("reporting.targets" if key in (rep.get("targets") or {})
                          else "report_spec.targets")
                mismatches.append(Mismatch(
                    f"{source}.{key}", str(band),
                    "the target's metric has no data in the loaded table",
                    "target dropped from scorecards"))
            else:
                targets[key] = band

    out_stages, stage_mm = guard_stages(weekly, stages)
    if stage_mm is not None:
        mismatches.append(stage_mm)

    return Framing(kpi_label, targets, out_stages, mismatches)


def guard_stages(weekly: pd.DataFrame, stages: pd.DataFrame | None
                 ) -> tuple[pd.DataFrame | None, Mismatch | None]:
    """The stages check alone (for surfaces that load stages independently): an
    applicant-pipeline frame whose date window never overlaps the loaded data is
    another engagement's leftover file — returns ``(None, Mismatch)``."""
    if (stages is None or not len(stages) or "date" not in stages.columns
            or not len(weekly)):
        return stages, None
    smin, smax = stages["date"].min(), stages["date"].max()
    wmin, wmax = weekly["date"].min(), weekly["date"].max()
    if smax < wmin or smin > wmax:
        return None, Mismatch(
            "data.pipeline_stages_path",
            f"stage dates {smin:%Y-%m-%d}..{smax:%Y-%m-%d}",
            f"never overlaps the loaded data ({wmin:%Y-%m-%d}..{wmax:%Y-%m-%d}) — "
            "likely another engagement's applicant-pipeline file",
            "funnel section dropped")
    return stages, None


def require_clean(framing, artifact: str = "the client report"):
    """Raise ``FramingError`` when mismatches exist — for shippable artifacts.

    Duck-typed: accepts ``Framing`` or ``ResolvedFraming`` (anything with
    ``.mismatches``)."""
    if framing.mismatches:
        raise FramingError(framing.mismatches, artifact)
    return framing


# ================================================================ resolver core
# The full framing resolver (design: docs/design-intake-agent.md). guard_framing
# above is the legacy two-layer guard kept for its tests; surfaces call
# resolve_framing / resolve, which layer config > engagement > spec > defaults and
# apply the same data guards to the WINNING value per field.

@dataclass
class ResolvedFraming:
    """What the surfaces should actually render, plus how it was decided."""
    kpi_metric: str
    kpi_label: str
    targets: dict
    stages: pd.DataFrame | None
    funnel_steps: list[str]
    client_name: str | None
    campaign_name: str | None
    budget: dict | None
    status: str                      # "confirmed" | "unconfirmed" | "invalid"
    mode: str                        # "lenient" | "strict"
    sources: dict[str, str]          # per field: config|engagement|spec|default
    client_target_keys: frozenset    # target keys banded by config OR engagement
    hidden_blocks: frozenset         # Overview blocks to hide when not confirmed
    mismatches: list[Mismatch] = field(default_factory=list)
    engagement_note: str | None = None


class UnconfirmedFramingError(FramingError):
    """The report gate: framing was never confirmed (or is invalid) for this data."""

    def __init__(self, resolved: "ResolvedFraming",
                 artifact: str = "the client report"):
        self.mismatches = resolved.mismatches
        lines = "\n".join(f"  - {m}" for m in resolved.mismatches) or \
            "  (no field mismatches — framing was simply never confirmed)"
        RuntimeError.__init__(
            self,
            f"refusing to build {artifact}: report framing is {resolved.status} for "
            f"the loaded data.\n{lines}\n"
            "Confirm framing on the dashboard Setup page, or pass "
            "--allow-unconfirmed for a watermarked draft.")


def _col_backed(df: pd.DataFrame, col: str) -> bool:
    return col in df.columns and df[col].notna().any()


def _nat_metrics(weekly: pd.DataFrame) -> dict | None:
    """National metric values keyed by metric name; None on compute failure (the
    guard's job is stale config, not metric plumbing — never false-positive)."""
    try:
        reg = M.load_metric_registry()
        return {r["metric"]: r
                for r in M.compute_metrics(weekly, by=None, registry=reg)
                .to_dict("records")}
    except Exception:
        return None


def _target_dead(nat: dict | None, key: str) -> bool:
    if nat is None:
        return False
    rec = nat.get(key)
    return rec is None or pd.isna(rec["value"]) or float(rec["value"]) == 0.0


def resolve(weekly: pd.DataFrame, *, cfg: dict | None = None,
            engagement: dict | None = None, spec: dict | None = None,
            stages: pd.DataFrame | None = None,
            mode: str | None = None) -> ResolvedFraming:
    """Pure framing resolution — no file I/O (hermetic by construction).

    Per-field cascade ``config > engagement > spec > deterministic default``; every
    candidate is validated (vocabulary) then guarded (data-backed); a failing
    candidate emits a layer-prefixed ``Mismatch`` and resolution falls to the next
    layer, ending at neutral defaults. The spec's judgment fields participate only
    once a confirmation basis exists (engagement.yaml, or guard-passing config in
    lenient mode) — unconfirmed surfaces show neutral defaults, never agent guesses.

    ``status``: ``confirmed`` = basis exists and no confirmed-layer field failed;
    ``invalid`` = basis exists but a confirmed-layer field is contradicted by the
    data; ``unconfirmed`` = no basis. The stored engagement data hash is provenance
    only — a data refresh alone NEVER retriggers intake, only a guard failure does.
    """
    cfg = cfg or {}
    rep = cfg.get("reporting") or {}
    eng = (engagement or {}).get("framing") or {}
    engagement_ok = bool(eng)
    spec = spec or {}
    mode = mode or rep.get("intake_mode") or DEFAULT_INTAKE_MODE
    if mode not in INTAKE_MODES:
        mode = DEFAULT_INTAKE_MODE
    mismatches: list[Mismatch] = []
    sources: dict[str, str] = {}
    measured = insights._has_measured(weekly)

    def _present(v) -> bool:
        return v not in (None, "", {}, [])

    config_framing = any(_present(rep.get(k)) for k in _FRAMING_KEYS)
    basis = engagement_ok or (mode == "lenient" and config_framing)

    def _layers(name, *, spec_key: str | None = None):
        """Candidate (layer, source, value) triples, highest precedence first."""
        out = []
        if _present(rep.get(name)):
            out.append(("config", f"reporting.{name}", rep.get(name)))
        if _present(eng.get(name)):
            out.append(("engagement", f"engagement.{name}", eng.get(name)))
        sk = spec_key or name
        if basis and _present(spec.get(sk)):
            out.append(("report_spec", f"report_spec.{sk}", spec.get(sk)))
        return out

    # --- kpi_metric -------------------------------------------------------------
    kpi_metric = None
    for layer, src, val in _layers("kpi_metric"):
        v = str(val).strip()
        if v not in PLUMBED_KPI_METRICS:
            mismatches.append(Mismatch(
                src, v, "not a plumbed metric key (v1 supports key_events / "
                "conversions)", "ignored"))
            continue
        if not _col_backed(weekly, v):
            mismatches.append(Mismatch(
                src, v, "the metric's column has no data in the loaded table",
                "field reset to the data-implied default"))
            continue
        kpi_metric, sources["kpi_metric"] = v, layer
        break
    if kpi_metric is None:
        kpi_metric = "key_events" if measured else "conversions"
        sources["kpi_metric"] = "default"

    # --- kpi_label --------------------------------------------------------------
    # A label decorates a metric. A layer that names its own (valid) kpi_metric may
    # label THAT metric; a bare label claims the measured yardstick (key_events) —
    # which reproduces the original guard exactly.
    kpi_label = None
    layer_metric = {"config": rep.get("kpi_metric"),
                    "engagement": eng.get("kpi_metric"), "report_spec": None}
    for layer, src, val in _layers("kpi_label"):
        label = str(val).strip()
        if label.lower() == DEFAULT_KPI_LABEL:
            kpi_label, sources["kpi_label"] = DEFAULT_KPI_LABEL, layer
            break
        lm = str(layer_metric.get(layer) or "").strip()
        claimed = lm if lm in PLUMBED_KPI_METRICS else "key_events"
        if claimed == "key_events" and not measured:
            mismatches.append(Mismatch(
                src, label,
                "no analytics-measured outcome series (key_events) exists in the "
                "loaded data",
                "label dropped; surfaces fall back to platform-claimed conversions"))
            continue
        if not _col_backed(weekly, claimed):
            mismatches.append(Mismatch(
                src, label, f"labels '{claimed}', which has no data in the loaded "
                "table", "label dropped"))
            continue
        kpi_label, sources["kpi_label"] = label, layer
        break
    if kpi_label is None:
        kpi_label, sources["kpi_label"] = DEFAULT_KPI_LABEL, "default"

    # --- targets (merged per key across participating layers) -------------------
    merged: dict = {}
    tsources: dict[str, str] = {}
    layer_maps = []
    if basis and isinstance(spec.get("targets"), dict):
        layer_maps.append(("report_spec", spec["targets"]))
    if isinstance(eng.get("targets"), dict):
        layer_maps.append(("engagement", eng["targets"]))
    if isinstance(rep.get("targets"), dict):
        layer_maps.append(("config", rep["targets"]))
    for layer, tmap in layer_maps:            # low -> high, higher overwrites
        for k, band in tmap.items():
            merged[k], tsources[k] = band, layer
    targets: dict = {}
    if merged:
        nat = _nat_metrics(weekly)
        for key, band in merged.items():
            if _target_dead(nat, key):
                mismatches.append(Mismatch(
                    f"{_LAYER_PREFIX[tsources[key]]}.targets.{key}", str(band),
                    "the target's metric has no data in the loaded table",
                    "target dropped from scorecards"))
            else:
                targets[key] = band
    client_target_keys = frozenset(
        k for k in targets if tsources.get(k) in ("config", "engagement"))
    sources["targets"] = next(
        (lay for lay in ("config", "engagement", "report_spec")
         if any(tsources.get(k) == lay for k in targets)), "default")

    # --- funnel_steps -----------------------------------------------------------
    funnel_steps = None
    for layer, src, val in _layers("funnel_steps"):
        if not isinstance(val, (list, tuple)):
            mismatches.append(Mismatch(src, str(val), "not a list of metric "
                                       "columns", "ignored"))
            continue
        kept = []
        for s in val:
            s = str(s).strip()
            if s not in M.BASE_INPUTS:
                mismatches.append(Mismatch(src, s, "not a known base metric column",
                                           "funnel step ignored"))
            elif not _col_backed(weekly, s):
                mismatches.append(Mismatch(src, s, "is no longer in the loaded data",
                                           "funnel step dropped"))
            else:
                kept.append(s)
        if kept:
            funnel_steps, sources["funnel_steps"] = kept, layer
            break
    if funnel_steps is None:
        funnel_steps = [c for c in M.FUNNEL_STAGES if _col_backed(weekly, c)]
        sources["funnel_steps"] = "default"

    # --- naming + budget (business judgments — layer-resolve only) --------------
    client_name = campaign_name = None
    for fieldname in ("client_name", "campaign_name"):
        for layer, _src, val in _layers(fieldname):
            v = str(val).strip()
            if v:
                if fieldname == "client_name":
                    client_name = v
                else:
                    campaign_name = v
                sources[fieldname] = layer
                break
        else:
            sources[fieldname] = "default"
    if client_name is None:
        client_name = (cfg.get("project") or {}).get("name")

    budget = None
    for layer, src, val in _layers("budget"):
        try:
            total = float(val["total"])
            weeks = float(val.get("flight_weeks") or 0)
            if total <= 0:
                raise ValueError
            budget = {"total": total,
                      **({"flight_weeks": weeks} if weeks > 0 else {})}
            sources["budget"] = layer
            break
        except (TypeError, KeyError, ValueError):
            mismatches.append(Mismatch(src, str(val),
                                       "not a valid {total, flight_weeks} plan",
                                       "budget ignored"))
    if budget is None:
        sources.setdefault("budget", "default")

    # --- stages + status + neutralization ---------------------------------------
    out_stages, stage_mm = guard_stages(weekly, stages)
    if stage_mm is not None:
        mismatches.append(stage_mm)

    confirmed_prefixes = ("engagement.",) if mode == "strict" else \
        ("engagement.", "reporting.")
    invalid = basis and any(m.source.startswith(confirmed_prefixes)
                            for m in mismatches)
    status = "invalid" if invalid else ("confirmed" if basis else "unconfirmed")

    if status != "confirmed":
        budget = None
        out_stages = None
    hidden = frozenset() if status == "confirmed" else HIDDEN_WHEN_UNCONFIRMED

    return ResolvedFraming(
        kpi_metric=kpi_metric, kpi_label=kpi_label, targets=targets,
        stages=out_stages, funnel_steps=funnel_steps, client_name=client_name,
        campaign_name=campaign_name, budget=budget, status=status, mode=mode,
        sources=sources, client_target_keys=client_target_keys,
        hidden_blocks=hidden, mismatches=mismatches)


# ---------------------------------------------------------------- engagement.yaml
def _normalize_framing(raw: dict) -> tuple[dict, list[str]]:
    """Tolerant normalization of an engagement framing block (read AND write path,
    so the file round-trips): unknown keys noted+ignored, bad values noted+unset —
    never raises. Data-aware validation happens later in ``resolve``."""
    notes: list[str] = []
    out: dict = {}
    for k, v in (raw or {}).items():
        if k not in _FRAMING_KEYS:
            notes.append(f"unknown key '{k}' ignored")
            continue
        out[k] = v
    km = out.get("kpi_metric")
    if km is not None and str(km).strip() not in PLUMBED_KPI_METRICS:
        notes.append(f"kpi_metric '{km}' not supported (v1: key_events/conversions)"
                     " — unset")
        out.pop("kpi_metric")
    if "funnel_steps" in out and not isinstance(out["funnel_steps"], (list, tuple)):
        notes.append("funnel_steps is not a list — unset")
        out.pop("funnel_steps")
    if "targets" in out:
        clean: dict = {}
        tmap = out["targets"] if isinstance(out["targets"], dict) else {}
        for k, band in tmap.items():
            try:
                clean[str(k)] = {kk: float(vv) for kk, vv in band.items()
                                 if kk in ("good", "warn", "goal")
                                 and vv is not None}
            except (TypeError, ValueError, AttributeError):
                notes.append(f"target '{k}' has a non-numeric band — dropped")
        out["targets"] = clean
    if "budget" in out:
        try:
            b = out["budget"]
            out["budget"] = {"total": float(b["total"]),
                             **({"flight_weeks": float(b["flight_weeks"])}
                                if b.get("flight_weeks") else {})}
        except (TypeError, KeyError, ValueError):
            notes.append("budget is not a valid {total, flight_weeks} plan — unset")
            out.pop("budget")
    for k in ("kpi_label", "client_name", "campaign_name"):
        if k in out:
            out[k] = str(out[k]).strip()
    return out, notes


def load_engagement(root: Path | None = None) -> tuple[dict, str | None]:
    """Read ``config/engagement.yaml`` -> ``({meta, framing}, note)``.

    ``load_active_spec``-shaped: missing file -> ``({}, None)``; unreadable or no
    framing block -> ``({}, note)``; otherwise the framing is normalized and any
    oddities are joined into the note (surfaced as ``engagement_note``)."""
    root = root or project_root()
    p = root / ENGAGEMENT_PATH
    if not p.exists():
        return {}, None
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception:
        return {}, ("config/engagement.yaml is unreadable — ignored; re-confirm on "
                    "the Setup page")
    if not isinstance(data, dict) or not isinstance(data.get("framing"), dict):
        return {}, ("config/engagement.yaml has no framing block — ignored; "
                    "re-confirm on the Setup page")
    framing, notes = _normalize_framing(data["framing"])
    return ({"meta": data.get("meta") or {}, "framing": framing},
            "; ".join(notes) or None)


def write_engagement(root: Path | None, framing: dict, *,
                     source: str = "intake_form") -> Path:
    """Write the confirmed intake answers (the Setup page's save path).

    Normalizes through the same reader rules (round-trip guarantee) and stamps
    provenance meta. The stored data hash is provenance ONLY ("confirmed against
    the July data") — validity is always the live guard, never the hash."""
    from ..agent import summaries                      # lazy: agent -> reporting cycle
    root = root or project_root()
    normalized, _notes = _normalize_framing(framing)
    payload = {
        "meta": {
            "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "confirmed_against_data_hash": summaries.data_hash(root),
            "source": source,
        },
        "framing": normalized,
    }
    p = root / ENGAGEMENT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Per-engagement report framing — written by the dashboard Setup page.\n"
        "# config.yaml keys still override these (escape hatch). Validity is the\n"
        "# live data guard, never the stored hash. docs/design-intake-agent.md.\n")
    p.write_text(header + yaml.safe_dump(payload, sort_keys=False,
                                         allow_unicode=True), encoding="utf-8")
    return p


# ---------------------------------------------------------------- convenience API
def resolve_framing(weekly: pd.DataFrame | None = None,
                    root: Path | None = None, *, cfg: dict | None = None,
                    spec: dict | None = None,
                    stages: pd.DataFrame | None = None,
                    mode: str | None = None) -> ResolvedFraming:
    """``resolve`` with lazy loading — the one call every surface makes.

    Fills only what wasn't passed: cfg via ``load_config``, spec via
    ``load_active_spec``, engagement via ``load_engagement``, weekly from the
    processed table (empty frame pre-pipeline -> status "unconfirmed", no crash).
    ``stages`` is never auto-loaded — pages without a funnel don't pay for one."""
    root = root or project_root()
    cfg = cfg if cfg is not None else load_config()
    if spec is None:
        from ..agent.spec_agent import load_active_spec    # lazy: avoids cycle
        spec, _note = load_active_spec(root)
    engagement, eng_note = load_engagement(root)
    if weekly is None:
        f = root / "data" / "processed" / "channel_weekly_metrics.csv"
        weekly = (pd.read_csv(f, parse_dates=["date"]) if f.exists()
                  else pd.DataFrame({"date": pd.Series(dtype="datetime64[ns]")}))
    res = resolve(weekly, cfg=cfg, engagement=engagement, spec=spec,
                  stages=stages, mode=mode)
    res.engagement_note = eng_note
    return res


def intake_status(root: Path | None = None,
                  weekly: pd.DataFrame | None = None) -> str:
    """"confirmed" | "unconfirmed" | "invalid" — derived from a full resolve."""
    return resolve_framing(weekly, root).status
