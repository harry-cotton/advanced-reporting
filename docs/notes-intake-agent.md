> **Status: discovery notes, not a plan.** Captured 2026-07-23 after loading a real client's
> data (CLIENTXYZ: Google, Meta, Snapchat — via the robust-ingestion fixes, see
> `docs/plan-robust-ingestion.md`) through a level-1 (descriptive, no-MMM) run. Intended as
> input to a Cowork brainstorm to shape the interaction, then a proper plan here.

# Notes: does the tool know what matters to a client?

## What happened

Loading CLIENTXYZ's real export data into the dashboard (previously seeded from a synthetic
FBI Talent Acquisition engagement) surfaced the report **still framed for FBI**, even though
the underlying campaign data was CLIENTXYZ's:

- The dashboard's most-valuable metric was **"application starts"** — the FBI recruiting KPI —
  with no equivalent in CLIENTXYZ's data at all.
- The **user-journey / funnel** section showed FBI's post-submission applicant-pipeline stages
  (`conditional_offers`, `final_offers`), which don't exist for CLIENTXYZ and correctly failed
  to join (`... did not join the weekly table` warnings), but the section still rendered.
- Written commentary still referenced the FBI framing.

## Root cause (confirmed, not assumed)

This was **not** a data leak — CLIENTXYZ's rows were correctly isolated in the store. It was
**stale engagement config** still pointed at the FBI setup, read verbatim by the reporting
layer with no check against what's actually in the loaded data:

- `config/config.yaml` → `reporting.kpi_label: application starts`, `data.kpi_path` +
  `data.pipeline_stages_path` pointing at FBI's CRM matchback / applicant-pipeline files,
  `reporting.client_name`/`campaign_name`, `budget`, `targets` — all FBI-specific.
- No `outputs/report_spec.json` existed, so the A1 spec agent (`agent/spec_agent.py`) had never
  run for this data — the FBI framing came **purely from hardcoded config**, confirming the
  agent path, if it had run, would have had a chance to catch this by reading the actual data.
- Fix applied this session: nulled/genericized the FBI-specific config fields (see the
  `config/config.yaml` diff in this session — not committed, since `config.yaml` is
  gitignored per-engagement config). The dashboard recovered correctly once the config was
  no longer lying about what the data contains.

**The generalizable finding:** nothing in the pipeline validates that a *configured* KPI,
target, or funnel stage actually exists in the *loaded* data before framing the whole report
around it. The dashboard already enforces a claims-vs-measured honesty principle for
platform-reported numbers (labeling below-campaign-grain data "platform-claimed") — but that
same discipline isn't applied to the report's own configured framing.

## The question this raises

Two bad extremes, neither of which is right:

- **Pure user-input form**: users often don't know their own metric taxonomy, or what's
  cleanly measurable in the data they handed you. A blank form produces vague or wrong answers.
- **Pure agent inference**: an agent silently picking a KPI/funnel from the data is the exact
  failure mode above, wearing a smarter hat — choosing a client's success metric is a business
  judgment call, not something inferable from column names alone.

## Proposed direction: agent proposes, human confirms

An intake/scoping step, not a chat, that runs once per dataset:

1. **Read the actual data** (pure pandas, no model call) — what channels exist, what
   outcome-like columns are present, what's measured vs. platform-claimed, what a plausible
   funnel looks like given the columns that actually joined.
2. **Propose a framing** grounded strictly in what's present: "your most complete outcome
   column looks like X; here's a funnel of A→B→C; currency is USD."
3. **Surface a short confirm/correct form** on the parts that are genuinely business
   decisions, not data facts: primary outcome + its value, which funnel steps matter, any
   target/benchmark.
4. **Answers become the per-engagement config** — explicit human answers win, same precedent
   the codebase already follows everywhere else (agent proposes, config overrides).

This matches the tool's stated agent philosophy verbatim (see `agent/AGENT_SYSTEM_BRIEF.md`):
*agents configure, the engine computes, agents narrate.* It's the natural extension of the
already-built A1 spec agent (`agent/spec_agent.py`, `agent/summaries.py`, `agent/validate.py`)
— which already produces `kpi_label`, targets, default tier, and block order from compact
computed summaries, with explicit config always winning. What's missing today is that it never
*asks* — it decides silently and nothing prompts a human to confirm before the report ships.

**One hard guard this also motivates, independent of the intake feature:** validate any
configured/spec'd KPI or funnel stage against what's actually present in the loaded data before
using it to frame the report. Block and prompt on mismatch — don't render a report framed
around a metric with zero rows behind it, the way this session's FBI leftover did.

## Practical questions raised, and current thinking

**Where would this run?**
A first-run screen in the Streamlit dashboard, keyed to the same data-hash the spec agent
already caches on (`agent/summaries.py`'s data hash) — so it runs once per dataset, not once
per page load. Sits ahead of `4_Explore`'s existing free-text lens box in the flow: pipeline
runs → intake page detects "no confirmed spec for this data hash" → shows the proposal +
confirm/correct form → answers write to config → the rest of the dashboard reads that config
exactly like it does today.

**Where do the questions surface?**
A structured confirm/correct form (editable fields: primary outcome, which funnel steps
matter, any target), not an open-ended chat. Matches the existing "config always wins"
pattern and is a much smaller, more predictable UI surface than a conversational interface.

**Can this work without an API key?**
Mostly yes. The *proposing* half — reading actual columns, detecting outcome-like fields,
checking measured-vs-platform-claimed — is pure pandas, zero API calls (same category of work
as the deterministic lens parser in `reporting/lens.py`, which is the default path today with
no key). Only the *narration* layer (turning the proposal into readable prose, handling
free-text answers) needs the LLM, and that's optional exactly like `agent.enabled` is today —
no key gets a plainer structured form; a key gets nicer phrasing. **The core capability does
not require a key; a key only upgrades the writing quality.**

## Suggested next step

Shape the interaction in Cowork first (form layout, exact trigger condition, persistence
model per client) — this is a UX decision more than an engineering one at this stage — then
bring the shaped design back here for an implementation plan, following the same pattern as
`docs/plan-robust-ingestion.md`.

## Not covered here

- The exact schema of the per-engagement "confirmed spec" file and how it interacts with the
  existing `outputs/report_spec.json` cache-by-data-hash mechanism.
- Whether the hard guard (configured KPI must exist in loaded data) ships independently of the
  intake UI, or only as part of it. Worth doing independently and sooner — it's a small,
  clearly-scoped validation, unlike the intake UI which needs design work first.
