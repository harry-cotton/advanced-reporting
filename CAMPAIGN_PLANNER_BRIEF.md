# Build brief — Campaign Planner

**For:** Claude Code (new session, Plan mode). **Owner:** Harry.
**Status:** spec from a Cowork brainstorm. Treat as the spec; ask before large architectural changes.

## Goal

Add a **planner** layer that turns *goals + rails* into a validated, data-grounded
`CampaignPlan`, which feeds the existing naming generator (Plan rows → names + UTMs).
The LLM must **not invent** recommendations or budgets — it selects and justifies against
evidence; the **numbers come from a deterministic optimizer**. Same uncertainty-aware,
context-engineered spine as the rest of the project.

Design stance (don't reverse without discussion): **no heavy agent framework** — a thin
"LLM-calls-tools" loop in plain code; stay model-/MCP-agnostic; deterministic default path
with a **guarded LLM path** (mirror `reporting/lens.py`). Keep everything pluggable behind
clean interfaces, the way `mmm/factory.py` swaps engines.

## Where it slots in

New package `src/advanced_reporting/planner/`, mirroring the `ingestion/` and `mmm/` layering.
Consumes evidence from the durable store + a fitted `MMMResult`; produces a `CampaignPlan`
that maps **exactly** onto the naming generator's Plan columns
(`market, channel, objective, audience_type, audience_detail, creative, format, size, placement, version`
— see `naming/naming_generator.py`).

## Components to build

1. **`schema.py` — `CampaignPlan` (typed, dataclasses/pydantic).**
   Hierarchy: campaign meta (`client, market, campaign, flight dates, total_budget, primary_kpi`)
   → funnel stages (`objective`) → platforms (`channel`) → audiences (`audience_type`,
   `audience_detail`) → creatives / sizes / placements, with a `budget` on each allocatable node
   and a `rationale` + `evidence_ref` + `confidence` on each recommendation.
   Must provide **`to_plan_rows()`** emitting exactly the generator's Plan columns, so planner
   output feeds the generator with zero glue. Round-trip this in tests.

2. **`config/planner_rails.yaml` — the rails (committed, no secrets).**
   Allowed platforms, the audience library, caps (e.g. max audiences/stage), budget-split
   rules (min/max per channel, total must reconcile), brand-safety, and a pointer to the naming
   vocab. These are **hard constraints**, enforced deterministically.

3. **`evidence.py` — deterministic evidence tools (return compact, structured data + provenance + confidence).**
   - `historical_performance(...)` — CPA / ROAS / CVR by `channel × segment` from the store.
     *Note:* demo-level grounding ("Meta for 35+") needs a demographic/audience breakdown the
     canonical schema doesn't carry yet — add that to `ingestion/schema.py` as a dependency, or
     stub with a clear `NotImplementedError` for now.
   - `platform_forecasts(...)` — reach/conversion forecasts via the platform planners behind the
     **existing connector pattern** (`ingestion/connectors.py`): Google Ads `ReachPlanService` /
     `PerformancePlannerService`, Meta reach & delivery estimates, TikTok/LinkedIn audience
     forecasts. Skeletons that `raise NotImplementedError` with exact wiring notes (verify current
     endpoint names at build time).
   - `response_curves(mmm_result)` — incremental return + saturation per channel from the fitted
     MMM. This is the **first-party, incrementality-grounded** input (platform numbers are
     walled-garden/self-credited → use them for reach/feasibility, never cross-channel allocation).

4. **`allocator.py` — deterministic budget split.**
   Optimize budget against the MMM response curves subject to the rails (min/max, totals).
   **Numbers originate here, not the LLM.** No fitted MMM available → fall back to a rules split
   (even / prior-weighted), explicitly flagged as low-confidence.

5. **`planner.py` — the (thin) core.**
   `plan_campaign(goals, rails) -> CampaignPlan`: gather evidence → propose the *qualitative* plan
   (funnel shape, which audiences, creative angles) → allocator sets budgets → validate.
   Deterministic rules default so it runs with no LLM; **guarded optional LLM path** for the
   qualitative selection/justification (same pattern as `lens.py`).

6. **`validate.py` — rails enforcement.**
   Check the `CampaignPlan` against the rails; reject or repair out-of-bounds; ensure completeness.
   Never trust the LLM to self-police.

## Context engineering (required)

Feed the LLM **only** goals + rails + the compact evidence — never raw store data or full tables.
Structured inputs in, structured plan out. Treat **context size + token cost as a tracked metric**.
See memory note: agentic-layer-design-stance.

## Tracing & evals

- Trace every LLM call: inputs, the choice made, evidence cited, confidence, cost.
- Eval harness in `tests/`: rails-compliance (caps/splits respected), completeness, and — where
  ground truth exists — the generated trafficking sheet as an **answer key** (planner → generator
  round-trip is directly gradeable).

## Tests (pytest, `tests/`)

- `to_plan_rows()` maps onto the generator's columns (round-trip with `naming_generator`).
- `validate` rejects rail violations and repairs where it can.
- `allocator` respects min/max, sums to total, and degrades gracefully with no MMM.
- `plan_campaign` deterministic path yields a valid `CampaignPlan` from a sample goals+rails.

## Deploy note

Target: **containerized, cloud-agnostic; decide host later.** Keep model access behind an
interface (Bedrock on AWS / Vertex on GCP / direct API are then a one-line swap). Don't bake in a
cloud now.

## Out of scope (now)

Real platform-forecast API calls (stubs only), the demographic schema extension (note as a
dependency), and the browser-agent executor. Don't unguard `meridian_engine.py`.

## Suggested kickoff for Claude Code

> Read `CAMPAIGN_PLANNER_BRIEF.md`. In Plan mode, propose the `planner/` package and the
> `CampaignPlan` schema, confirm the `to_plan_rows()` mapping against `naming/naming_generator.py`,
> then implement with the deterministic default + guarded LLM path and the tests above. Keep it
> pluggable; don't unguard Meridian.
