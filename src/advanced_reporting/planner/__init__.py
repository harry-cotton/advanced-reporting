"""Campaign planner: goals + rails -> validated ``CampaignPlan`` -> naming generator.

Deterministic by default; a guarded optional LLM path proposes the *qualitative* plan while a
deterministic optimizer owns every budget number. Pluggable, source/engine-agnostic — mirrors
the ``ingestion/`` and ``mmm/`` layers.
"""
from __future__ import annotations

from .factory import get_planner
from .naming_bridge import write_plan_xlsx
from .planner import plan_campaign
from .rails import load_rails
from .schema import (Audience, CampaignPlan, Creative, FunnelStage, Platform,
                     PlannerTrace, Recommendation, PLAN_COLS)
from .validate import PlannerValidationError, check, enforce

__all__ = [
    "plan_campaign", "get_planner", "load_rails", "write_plan_xlsx",
    "CampaignPlan", "FunnelStage", "Platform", "Audience", "Creative",
    "Recommendation", "PlannerTrace", "PLAN_COLS",
    "check", "enforce", "PlannerValidationError",
]
