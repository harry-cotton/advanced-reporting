"""The ``CampaignPlan`` — the planner's typed output.

A `CampaignPlan` is a budget-allocation hierarchy: campaign meta -> funnel stages
(``objective``) -> platforms (``channel``) -> audiences (``audience_type`` /
``audience_detail`` / ``placement``) -> creatives (``creative`` / ``format`` / ``size`` /
``version``). Every **allocatable node** carries a ``budget`` (set by the deterministic
``allocator``, never the LLM) and a ``Recommendation`` (rationale + evidence pointer +
confidence), so each choice is auditable.

The whole point is ``to_plan_rows()``: it emits exactly the columns the naming generator's
**Plan** sheet expects (``naming/naming_generator.py`` ``PLAN_COLS``), so planner output feeds
the generator with zero glue. ``PLAN_COLS`` is duplicated here as the canonical contract and
cross-checked against the generator in the tests (the generator lives at the repo root, not in
this package, so we avoid importing it at runtime).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict


class PlannerValidationError(ValueError):
    """Raised when a plan violates the rails in a way ``enforce`` cannot repair,
    or when the goals/rails combination is infeasible before allocation."""

# The naming generator's Plan-sheet columns, verbatim and in order. Kept in sync with
# ``naming/naming_generator.py:PLAN_COLS`` by ``tests/test_planner.py`` (round-trip).
PLAN_COLS = ["market", "channel", "objective", "audience_type", "audience_detail",
             "creative", "format", "size", "placement", "version"]


@dataclass
class Recommendation:
    """Why a node is in the plan: a hedged rationale, an evidence pointer, a 0..1 confidence."""
    rationale: str = ""
    evidence_ref: str = ""        # provenance pointer, e.g. "response_curves:meta"
    confidence: float = 0.0       # 0..1


@dataclass
class Creative:
    creative: str
    format: str
    size: str
    version: str = "V1"
    budget: float | None = None
    rec: Recommendation = field(default_factory=Recommendation)


@dataclass
class Audience:
    audience_type: str
    audience_detail: str
    placement: str = ""
    budget: float | None = None
    creatives: list[Creative] = field(default_factory=list)
    rec: Recommendation = field(default_factory=Recommendation)


@dataclass
class Platform:
    """A channel (e.g. ``meta``) within a funnel stage."""
    channel: str
    budget: float | None = None
    audiences: list[Audience] = field(default_factory=list)
    rec: Recommendation = field(default_factory=Recommendation)


@dataclass
class FunnelStage:
    """A funnel stage, identified by its ``objective`` (e.g. ``AWARENESS``)."""
    objective: str
    budget: float | None = None
    platforms: list[Platform] = field(default_factory=list)
    rec: Recommendation = field(default_factory=Recommendation)


@dataclass
class PlannerTrace:
    """One planning run's trace: the choice made, evidence cited, confidence, and LLM cost.

    The brief requires every LLM call to be traced (inputs, choice, evidence, confidence,
    cost) and treats context size + token cost as a tracked metric. The deterministic path
    produces a trace too (``model=None``, ``cost_usd=0``).
    """
    source: str = "deterministic"          # "deterministic" | "llm"
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    context_tokens: int = 0                 # tracked metric: prompt size fed to the LLM
    cost_usd: float = 0.0
    confidence: float = 0.0
    evidence_refs: list[str] = field(default_factory=list)
    choice: dict = field(default_factory=dict)   # the qualitative selection that was made
    notes: str = ""


@dataclass
class CampaignPlan:
    client: str
    market: str
    campaign: str
    flight_start: str
    flight_end: str
    total_budget: float
    primary_kpi: str
    stages: list[FunnelStage] = field(default_factory=list)
    trace: PlannerTrace = field(default_factory=PlannerTrace)

    # -- iteration helpers -------------------------------------------------------------
    def iter_creatives(self):
        """Yield ``(stage, platform, audience, creative)`` for every leaf in the plan."""
        for st in self.stages:
            for pf in st.platforms:
                for au in pf.audiences:
                    for cr in au.creatives:
                        yield st, pf, au, cr

    # -- the naming-generator contract -------------------------------------------------
    def to_plan_rows(self) -> list[dict]:
        """Emit one dict per creative with EXACTLY the generator's ``PLAN_COLS`` keys.

        This is the zero-glue handoff: write these as rows of a ``Plan`` sheet and
        ``naming_generator.generate`` turns them into campaign/ad-set/ad names + UTMs.
        Budgets are intentionally absent (the Plan sheet has no budget column).
        """
        rows = []
        for st, pf, au, cr in self.iter_creatives():
            rows.append({
                "market": self.market,
                "channel": pf.channel,
                "objective": st.objective,
                "audience_type": au.audience_type,
                "audience_detail": au.audience_detail,
                "creative": cr.creative,
                "format": cr.format,
                "size": cr.size,
                "placement": au.placement,
                "version": cr.version,
            })
        return rows

    def to_budget_table(self) -> list[dict]:
        """The deterministic budget export: one row per creative with the money + provenance."""
        rows = []
        for st, pf, au, cr in self.iter_creatives():
            rows.append({
                "objective": st.objective,
                "channel": pf.channel,
                "audience_type": au.audience_type,
                "audience_detail": au.audience_detail,
                "creative": cr.creative,
                "budget": cr.budget,
                "confidence": cr.rec.confidence,
                "evidence_ref": cr.rec.evidence_ref,
                "rationale": cr.rec.rationale,
            })
        return rows

    def to_dict(self) -> dict:
        """Plain nested dict (e.g. for ``json.dump`` to ``outputs/``)."""
        return asdict(self)
