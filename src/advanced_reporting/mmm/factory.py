"""Select an MMM engine by name so the rest of the pipeline stays engine-agnostic."""
from __future__ import annotations
from .base import BaseMMM


def get_engine(name: str | None = None, **kwargs) -> BaseMMM:
    name = (name or "baseline").lower()
    if name == "baseline":
        from .baseline import BaselineMMM
        return BaselineMMM(**kwargs)
    if name == "meridian":
        from .meridian_engine import MeridianMMM
        return MeridianMMM(**kwargs)
    raise ValueError(f"Unknown MMM engine '{name}'. Use 'baseline' or 'meridian'.")
