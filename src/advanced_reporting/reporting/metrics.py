"""Tiered metric taxonomy (the "KPI pyramid") and campaign-goal resolution.

Loads the metric registry (``config/metrics.yaml``) and campaign goal tags
(``config/campaign_goals.yaml``), and computes metrics from the weekly tables produced by
``transform/clean.py``. Metrics are ratios of AGGREGATE totals (e.g. CTR = sum(clicks) /
sum(impressions)), computed at whatever grouping you ask for (national / per-channel /
per-geo). Intent-tier metrics need engagement columns (sessions, ...); where those were
never measured they evaluate to NaN rather than a misleading zero.
"""
from __future__ import annotations
import math
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from ..utils import project_root

TIERS = ("reach", "intent", "outcome")

# Base quantities a formula may reference (summed over the grouping).
# conditional_offers / final_offers are post-submission pipeline VOLUMES (CRM calendar-
# week counts) — counts only, never a cost denominator (the gates lag media by months).
BASE_INPUTS = ("spend", "impressions", "clicks", "conversions", "platform_revenue",
               "sessions", "engaged_sessions", "page_views", "video_views", "key_events",
               "conditional_offers", "final_offers")

_VALID_FORMATS = ("count", "currency", "pct", "ratio")


def _config_path(name: str, path=None) -> Path:
    return Path(path) if path is not None else project_root() / "config" / name


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_metric_registry(path=None) -> list[dict]:
    """Return (and validate) the list of metric specs from ``config/metrics.yaml``."""
    metrics = _load_yaml(_config_path("metrics.yaml", path)).get("metrics", [])
    for m in metrics:
        for field in ("key", "label", "tier", "formula", "format"):
            if field not in m:
                raise ValueError(f"metric {m.get('key')!r}: missing '{field}'")
        if m["tier"] not in TIERS:
            raise ValueError(f"metric {m['key']!r}: tier must be one of {TIERS}")
        if m["format"] not in _VALID_FORMATS:
            raise ValueError(f"metric {m['key']!r}: format must be one of {_VALID_FORMATS}")
    return metrics


def load_campaign_goals(path=None) -> dict:
    """Return the campaign-goal config (overrides, inference, default, tier map)."""
    return _load_yaml(_config_path("campaign_goals.yaml", path))


def resolve_goal(campaign: str, goals: dict | None = None) -> str:
    """Resolve a campaign name to a goal: explicit override -> name inference -> default.

    Overrides match as whole tokens/phrases anywhere in the name (so 'nonbrand' fires
    inside 'NonBrand_Search_US' — real campaign names are compound), checked longest
    key first so 'nonbrand' beats 'brand'. Inference patterns match at token STARTS
    ('retarget' fires for 'retargeting', but 'mid' no longer fires inside 'midwest'
    and 'brand' no longer fires inside 'nonbrand')."""
    from ..utils import norm_text, phrase_in
    goals = goals if goals is not None else load_campaign_goals()
    name = str(campaign).strip().lower()
    tokens = norm_text(name).split()

    overrides = {str(k).lower(): v for k, v in (goals.get("overrides") or {}).items()}
    if name in overrides:                      # exact full-name override, highest priority
        return overrides[name]
    for key in sorted(overrides, key=len, reverse=True):
        if phrase_in(name, key):
            return overrides[key]

    for goal, patterns in (goals.get("inference") or {}).items():
        for p in (patterns or []):
            p = str(p).lower()
            # short generic patterns ('mid', 'abm') must match a whole token; longer
            # stems may prefix-match ('retarget' -> 'retargeting')
            if any(t == p or (len(p) >= 4 and t.startswith(p)) for t in tokens):
                return goal
    return goals.get("default_goal", "conversion")


def primary_tier(goal: str, goals: dict | None = None) -> str:
    """The pyramid tier a goal is primarily scored on (awareness->reach, etc.)."""
    goals = goals if goals is not None else load_campaign_goals()
    return (goals.get("goal_primary_tier") or {}).get(goal, "outcome")


def _evaluate(formula: str, totals: dict) -> float:
    """Evaluate a registry formula against summed base totals.

    Returns NaN on divide-by-zero or any missing/non-numeric input. The namespace is
    restricted to the base quantities (no builtins); formulas come from committed config.
    """
    ns = {k: float(totals.get(k, np.nan)) for k in BASE_INPUTS}
    try:
        return float(eval(formula, {"__builtins__": {}}, ns))  # noqa: S307 - trusted config
    except ZeroDivisionError:
        return float("nan")
    except Exception:
        return float("nan")


def _totals(df: pd.DataFrame) -> dict:
    """Sum each present base column. A column that is entirely NaN -> NaN (not 0), so a
    never-measured tier reads as 'not measured' rather than zero."""
    out = {}
    for c in BASE_INPUTS:
        if c in df.columns:
            out[c] = float(pd.to_numeric(df[c], errors="coerce").sum(min_count=1))
    return out


def compute_metrics(weekly: pd.DataFrame, *, by: str | None = None,
                    registry: list[dict] | None = None) -> pd.DataFrame:
    """Compute every registry metric over ``weekly``, returning a tidy long frame.

    ``by``: None -> one national row-set (totals over the whole table); ``"channel"`` or
    ``"geo"`` -> one metric set per group value. Columns: basis, key, metric, label, tier,
    value, format, higher_is_better.
    """
    registry = registry if registry is not None else load_metric_registry()
    if by is None:
        groups, basis = [("all", weekly)], "national"
    else:
        if by not in weekly.columns:
            raise KeyError(f"weekly table has no '{by}' column for by={by!r}")
        groups, basis = list(weekly.groupby(by)), by

    rows = []
    for key, g in groups:
        totals = _totals(g)
        for m in registry:
            rows.append({
                "basis": basis, "key": str(key),
                "metric": m["key"], "label": m["label"], "tier": m["tier"],
                "value": _evaluate(m["formula"], totals),
                "format": m["format"], "higher_is_better": bool(m.get("higher_is_better", True)),
            })
    cols = ["basis", "key", "metric", "label", "tier", "value", "format", "higher_is_better"]
    return pd.DataFrame(rows, columns=cols)


def tag_campaign_goals(campaigns, goals: dict | None = None) -> pd.DataFrame:
    """Map campaign names to (campaign, goal, primary_tier), de-duplicated."""
    goals = goals if goals is not None else load_campaign_goals()
    uniq = pd.unique(pd.Series(list(campaigns), dtype="object"))
    out = pd.DataFrame({"campaign": uniq})
    out["goal"] = out["campaign"].map(lambda c: resolve_goal(c, goals))
    out["primary_tier"] = out["goal"].map(lambda gl: primary_tier(gl, goals))
    return out


# --- Dashboard helpers -------------------------------------------------------------

FUNNEL_STAGES = ["impressions", "clicks", "sessions", "engaged_sessions", "conversions"]
_STAGE_LABEL = {
    "impressions": "Impressions", "clicks": "Clicks", "sessions": "Sessions",
    "engaged_sessions": "Engaged sessions", "conversions": "Conversions",
}


def funnel(weekly: pd.DataFrame) -> pd.DataFrame:
    """National funnel volumes + step pass-through rates (the drop-off view).

    Each stage's ``step_rate`` is stage / previous available stage. Stages whose column
    is absent or never measured (engagement may be missing) are skipped, so the funnel
    collapses gracefully to whatever stages exist.
    """
    totals = _totals(weekly)
    rows, prev_val, prev_stage = [], None, None
    for stage in FUNNEL_STAGES:
        v = totals.get(stage)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        rate = (v / prev_val) if (prev_val not in (None, 0)) else float("nan")
        rows.append({"stage": stage, "label": _STAGE_LABEL.get(stage, stage),
                     "value": float(v), "from": prev_stage, "step_rate": rate})
        prev_val, prev_stage = v, stage
    return pd.DataFrame(rows, columns=["stage", "label", "value", "from", "step_rate"])


def _money(x: float) -> str:
    ax = abs(x)
    if ax >= 1e6:
        return f"${x/1e6:.2f}M"
    if ax >= 1e3:
        return f"${x/1e3:.1f}k"
    return f"${x:,.2f}"


def format_value(value, fmt: str) -> str:
    """Render a metric value per its format (pct | currency | count | ratio); NaN -> em dash."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    if fmt == "pct":
        return f"{value*100:.1f}%"
    if fmt == "currency":
        return _money(value)
    if fmt == "count":
        return f"{value:,.0f}"
    if fmt == "ratio":
        return f"{value:.2f}x"
    return f"{value:,.2f}"


def pyramid(weekly: pd.DataFrame, *, registry=None) -> dict:
    """Group national metrics by tier for the pyramid view: ``{tier: [metric records]}``."""
    long = compute_metrics(weekly, by=None, registry=registry)
    return {tier: long[long["tier"] == tier].to_dict("records") for tier in TIERS}
