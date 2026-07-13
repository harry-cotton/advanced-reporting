"""Load + validate a synthetic-engagement scenario spec (the DGP bible).

A scenario (``config/scenarios/*.yaml``) is the single source of truth for a synthetic
engagement: channels, budget shares, flighting, per-channel adstock/Hill/true-ROI,
funnel rates, geos, seasonality, controls, stress cases, the applicant pipeline, the
unparsed-name tail, and the naming vocab. ``ingestion/scenario_dgp.py`` (P1) consumes
it; this module only LOADS and structurally VALIDATES it so a malformed spec fails loud
and early — never silently generating nonsense data.

``validate_scenario`` returns the list of problems (empty == valid) rather than raising,
so callers can show them all at once; ``load_scenario`` raises ``ScenarioError`` on a
spec that doesn't validate. The checks are structural + arithmetic-consistency (shares
sum to ~1, flight weeks match the dates, pipeline stages are covered) — not a full JSON
Schema, kept light per the project's dependency budget.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from ..utils import project_root

DEFAULT_SCENARIO = "fbi_recruitment"
_SUM_TOL = 0.01                       # shares must sum to 1.0 within this


class ScenarioError(ValueError):
    """A scenario spec that is missing, unreadable, or fails structural validation."""


def scenario_path(name: str | Path = DEFAULT_SCENARIO) -> Path:
    """Resolve a scenario name (or path) to a YAML file under config/scenarios/."""
    p = Path(name)
    if p.suffix in (".yaml", ".yml"):
        return p
    return project_root() / "config" / "scenarios" / f"{name}.yaml"


def load_scenario(name: str | Path = DEFAULT_SCENARIO) -> dict:
    """Load and validate a scenario spec; raise ``ScenarioError`` if it doesn't validate."""
    path = scenario_path(name)
    if not path.exists():
        raise ScenarioError(f"scenario spec not found: {path}")
    spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    problems = validate_scenario(spec)
    if problems:
        joined = "\n  - ".join(problems)
        raise ScenarioError(f"{path.name} failed validation:\n  - {joined}")
    return spec


# ---------------------------------------------------------------- validation
_TOP_LEVEL = ("name", "seed", "meta", "flight", "budget", "initiatives", "geos",
              "channels", "funnel", "pipeline", "unparsed_tail", "naming_vocab")
_CHANNEL_KINDS = {"paid", "nonpaid"}
_GRAINS = {"ad_group", "ad_set", "creative", "campaign"}


def _approx_one(values, tol: float = _SUM_TOL) -> bool:
    return abs(sum(values) - 1.0) <= tol


def _as_date(v) -> date | None:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        return None


def validate_scenario(spec: dict) -> list[str]:
    """Return a list of human-readable problems ([] == valid). Never raises on content."""
    problems: list[str] = []
    if not isinstance(spec, dict):
        return ["scenario is not a mapping"]

    for key in _TOP_LEVEL:
        if key not in spec:
            problems.append(f"missing top-level key: {key}")

    _validate_flight(spec.get("flight"), problems)
    _validate_initiatives(spec.get("initiatives"), problems)
    _validate_geos(spec.get("geos"), problems)
    paid = _validate_channels(spec.get("channels"), problems)
    _validate_pipeline(spec.get("pipeline"), problems)
    _validate_naming_vocab(spec.get("naming_vocab"), spec.get("initiatives"), problems)
    _validate_unparsed_tail(spec.get("unparsed_tail"), paid, problems)
    return problems


def _validate_flight(flight, problems: list[str]) -> None:
    if not isinstance(flight, dict):
        problems.append("flight: must be a mapping with start/end/weeks")
        return
    start, end = _as_date(flight.get("start")), _as_date(flight.get("end"))
    weeks = flight.get("weeks")
    if start is None or end is None:
        problems.append("flight: start/end must be ISO dates (YYYY-MM-DD)")
        return
    if start.weekday() != 0:
        problems.append(f"flight.start {start} is not a Monday (weekday={start.weekday()})")
    if end.weekday() != 6:
        problems.append(f"flight.end {end} is not a Sunday (weekday={end.weekday()})")
    span_days = (end - start).days + 1            # inclusive
    if span_days % 7 != 0:
        problems.append(f"flight span {span_days} days is not a whole number of weeks")
    computed = span_days // 7
    if not isinstance(weeks, int) or weeks != computed:
        problems.append(f"flight.weeks={weeks} disagrees with dates ({computed} complete weeks)")


def _validate_initiatives(inits, problems: list[str]) -> None:
    if not isinstance(inits, list) or not inits:
        problems.append("initiatives: must be a non-empty list")
        return
    shares = []
    for it in inits:
        if not isinstance(it, dict) or "code" not in it or "spend_share" not in it:
            problems.append(f"initiatives: each needs code + spend_share ({it!r})")
            continue
        shares.append(float(it["spend_share"]))
    if shares and not _approx_one(shares):
        problems.append(f"initiatives spend_share sums to {sum(shares):.4f}, not 1.0")


def _validate_geos(geos, problems: list[str]) -> None:
    if not isinstance(geos, list) or not geos:
        problems.append("geos: must be a non-empty list")
        return
    pops = []
    for g in geos:
        if not isinstance(g, dict) or "code" not in g:
            problems.append(f"geos: each needs a code ({g!r})")
            continue
        if "population" not in g or "base_multiplier" not in g:
            problems.append(f"geos: {g.get('code')} needs population + base_multiplier")
            continue
        if not str(g["code"]).startswith("US-"):
            problems.append(f"geos: code {g['code']} should be a US-* region code")
        pops.append(float(g["population"]))
    if pops and not _approx_one(pops, tol=0.02):
        problems.append(f"geo populations sum to {sum(pops):.4f}, not ~1.0")


def _validate_channels(channels, problems: list[str]) -> list[str]:
    """Validate channels; return the list of PAID channel names for cross-checks."""
    if not isinstance(channels, dict) or not channels:
        problems.append("channels: must be a non-empty mapping")
        return []
    paid, paid_shares = [], []
    for name, ch in channels.items():
        if not isinstance(ch, dict) or "kind" not in ch:
            problems.append(f"channels.{name}: needs a kind (paid|nonpaid)")
            continue
        kind = ch["kind"]
        if kind not in _CHANNEL_KINDS:
            problems.append(f"channels.{name}: kind {kind!r} not in {_CHANNEL_KINDS}")
        if kind == "paid":
            paid.append(name)
            for req in ("spend_share", "grain", "reader", "adstock_decay", "roi_apps_per_1k"):
                if req not in ch:
                    problems.append(f"channels.{name} (paid): missing {req}")
            if ch.get("grain") not in _GRAINS and "grain" in ch:
                problems.append(f"channels.{name}: grain {ch.get('grain')!r} not in {_GRAINS}")
            if "spend_share" in ch:
                paid_shares.append(float(ch["spend_share"]))
    if paid_shares and not _approx_one(paid_shares):
        problems.append(f"paid channel spend_share sums to {sum(paid_shares):.4f}, not 1.0")
    return paid


def _validate_pipeline(pipeline, problems: list[str]) -> None:
    if not isinstance(pipeline, dict):
        problems.append("pipeline: must be a mapping")
        return
    stages = pipeline.get("stages")
    if not isinstance(stages, list) or len(stages) != 6:
        problems.append(f"pipeline.stages: expected 6 major phases, got {stages!r}")
        stages = stages if isinstance(stages, list) else []
    paths = pipeline.get("paths")
    if not isinstance(paths, dict) or "SA" not in paths or "default" not in paths:
        problems.append("pipeline.paths: must include 'SA' and 'default'")
        return
    for pname, path in paths.items():
        for field in ("pass_rate", "lag_weeks"):
            table = path.get(field) if isinstance(path, dict) else None
            if not isinstance(table, dict):
                problems.append(f"pipeline.paths.{pname}.{field}: must be a mapping")
                continue
            for stage in stages:
                if stage not in table:
                    problems.append(f"pipeline.paths.{pname}.{field}: missing stage {stage}")
                elif field == "pass_rate":
                    r = table[stage]
                    if not (isinstance(r, (int, float)) and 0 < r <= 1):
                        problems.append(
                            f"pipeline.paths.{pname}.pass_rate.{stage}={r} not in (0,1]")


def _validate_naming_vocab(vocab, inits, problems: list[str]) -> None:
    if not isinstance(vocab, dict):
        problems.append("naming_vocab: must be a mapping")
        return
    for req in ("audiences", "creatives", "initiatives"):
        if req not in vocab:
            problems.append(f"naming_vocab: missing {req}")
    # vocab initiatives must match the declared career paths (the grammar dogfood)
    if isinstance(inits, list) and isinstance(vocab.get("initiatives"), list):
        declared = {it.get("code") for it in inits if isinstance(it, dict)}
        vocab_inits = set(vocab["initiatives"])
        if declared != vocab_inits:
            problems.append(f"naming_vocab.initiatives {sorted(vocab_inits)} != "
                            f"declared initiatives {sorted(declared)}")


def _validate_unparsed_tail(tail, paid: list[str], problems: list[str]) -> None:
    if not isinstance(tail, dict):
        problems.append("unparsed_tail: must be a mapping")
        return
    share = tail.get("spend_share")
    if not (isinstance(share, (int, float)) and 0 <= share < 0.5):
        problems.append(f"unparsed_tail.spend_share={share} should be a fraction in [0, 0.5)")
    for ch in tail.get("channels", []) or []:
        if paid and ch not in paid:
            problems.append(f"unparsed_tail.channels: {ch} is not a paid channel")
    if not tail.get("names"):
        problems.append("unparsed_tail.names: expected a few legacy names")
