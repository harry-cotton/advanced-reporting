"""Select a planning strategy by name, so callers stay strategy-agnostic.

Mirrors ``mmm/factory.py`` / ``ingestion/factory.py``: lazy import, name -> implementation,
default fallback, explicit ``ValueError``. One strategy today (``default`` = rules proposer +
guarded LLM + deterministic allocator), but new proposers/allocators plug in here.
"""
from __future__ import annotations


class _DefaultPlanner:
    """Thin wrapper exposing ``plan(goals, ...)`` over ``planner.plan_campaign``."""

    name = "default"

    def __init__(self, rails: dict | None = None):
        self.rails = rails

    def plan(self, goals: dict, **kwargs):
        from .planner import plan_campaign
        kwargs.setdefault("rails", self.rails)
        return plan_campaign(goals, **kwargs)


def get_planner(name: str | None = None, **kwargs):
    """Return a planner strategy. Use ``'default'`` (the only strategy today)."""
    name = (name or "default").lower()
    if name == "default":
        return _DefaultPlanner(**kwargs)
    raise ValueError(f"Unknown planner strategy '{name}'. Use 'default'.")
