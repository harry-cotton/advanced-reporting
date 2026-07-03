"""Load and access the planner rails (``config/planner_rails.yaml``).

The rails are committed structural config (no secrets) — the hard constraints the planner
enforces deterministically. This module just loads + validates them and offers a few small
accessors; mirrors ``reporting/metrics.py``'s ``_config_path`` / ``_load_yaml`` pattern.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from ..utils import project_root

_REQUIRED_SECTIONS = ("platforms", "budget_rules", "caps", "audience_library",
                      "funnel", "creatives", "llm", "naming")


def _config_path(name: str = "planner_rails.yaml", path=None) -> Path:
    return Path(path) if path is not None else project_root() / "config" / name


def load_rails(path=None) -> dict:
    """Return (and validate) the planner rails from ``config/planner_rails.yaml``."""
    with open(_config_path(path=path)) as fh:
        rails = yaml.safe_load(fh) or {}
    missing = [s for s in _REQUIRED_SECTIONS if s not in rails]
    if missing:
        raise ValueError(f"planner_rails.yaml missing required section(s): {missing}")
    if not rails.get("platforms"):
        raise ValueError("planner_rails.yaml: 'platforms' must list at least one channel")
    return rails


# --- small accessors (keep callers from hard-coding section/key names) -----------------

def allowed_channels(rails: dict) -> list[str]:
    return list(rails.get("platforms", []))


def channel_bounds(rails: dict, total_budget: float) -> tuple[float, float]:
    """Absolute (min, max) budget allowed per funded channel, from the pct rules."""
    br = rails.get("budget_rules", {})
    lo = float(br.get("min_pct_per_channel", 0.0)) * total_budget
    hi = float(br.get("max_pct_per_channel", 1.0)) * total_budget
    return lo, hi


def reconcile_tolerance(rails: dict) -> float:
    return float(rails.get("budget_rules", {}).get("reconcile_tolerance", 0.01))


def funnel_for_goal(rails: dict, goal: str) -> dict:
    """The ``{objective, lead_channels}`` block for a goal (falls back to conversion)."""
    funnel = rails.get("funnel", {})
    return funnel.get(goal) or funnel.get("conversion") or next(iter(funnel.values()), {})
