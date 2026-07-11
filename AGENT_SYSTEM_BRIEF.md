# Build brief — the Agent System (guided reporting)

**STATUS: APPROVED DESIGN, NOT YET BUILT** (agreed 2026-07-11).
**Owner:** Harry. Treat as the spec; ask before large architectural changes.

## The one-line design

**Agents CONFIGURE, the deterministic engine COMPUTES, agents NARRATE over computed
facts.** No LLM ever produces a number; no number ever appears in prose unless the
deterministic layer computed it first. This is the planner's proven pattern
("LLM selects and justifies, clipped to the rails; the allocator owns every budget")
promoted to the whole reporting product.

## The system knowledge base (`system/`)

The agent system runs on curated, human-authored knowledge — never on vibes. The
folder structure IS the mental model:

```
system/
  guidelines/     COMMITTED · Harry-authored, reusable across every client — the IP.
    campaign_types.md        what awareness/consideration/conversion/lead-gen/
                             recruitment campaigns look like; which KPIs lead
    conversion_types.md      platform-claimed vs analytics-measured vs CRM-verified;
                             when each is trustworthy; matchback rules
    metrics_playbook.md      per metric: definition, healthy ranges by context,
                             classic misreadings to avoid
    recommendation_menu.md   the ONLY recommendation types agents may issue, each
                             with its triggering condition + required evidence
  context/        GITIGNORED · per-client/per-engagement briefs (like data/).
    client_brief.md          who the client is, goals, budget, flight, KPI wiring
    macro_notes.md           curated external-context bullets (moves here from
                             config/macro_notes.md; same never-generated rule)
  prompts/        COMMITTED · the agent prompt templates (versioned like code).
    spec_agent.md
    commentary_agent.md
```

Rules: `guidelines/` changes are code reviews (they steer every client's output).
`context/` never enters git (client-confidential, like `data/`). Generated artifacts
go to `outputs/` (gitignored) — never into `system/`.

## Phase A1 — the report-spec agent (build first)

One guarded structured-output call (via the existing `src/advanced_reporting/llm.py`
gateway) at PIPELINE TIME — never per page load:

- **Inputs (compact summaries only, never raw rows** — the `evidence.py` rule):
  store manifest + schema signature, per-channel/per-campaign computed rollups,
  DQ report summary, unparsed-name rate, `system/guidelines/*`, `system/context/*`.
- **Output:** `outputs/report_spec.json` — campaign_type, kpi_label, primary tier,
  `reporting.targets`-shaped thresholds, insight-block selection/order, watch flags.
  **Validated** against the metric registry + config vocab (planner-rails style:
  clip, never trust); invalid fields fall back to defaults, loudly.
- The dashboard reads the spec exactly as it reads config today (spec fills the
  gaps config leaves; explicit config keys always win). No key / no spec → current
  behavior, unchanged. Cache per data-hash; a stale spec (hash mismatch) is ignored
  with a visible note.
- New module: `src/advanced_reporting/agent/` (`knowledge.py` loader, `spec_agent.py`,
  `validate.py`). CLI: `python scripts/advise.py --spec`.

**Acceptance:** drop a new scenario's exports → ingest → `advise.py --spec` →
pipeline → the dashboard arranges itself (right KPI label, right primary tier, right
blocks) with zero hand-config; all numbers byte-identical to a spec-less run.

## Phase A2 — the commentary & recommendations agent

Second guarded call, also offline, writing `outputs/commentary_ai.md`:

- **Inputs:** the computed insight payloads (`insights.py` dicts), tier scorecard,
  audience/creative rollups, MMM summary when present, guidelines + context.
- **Narrative rule:** prose may only restate computed facts. **Loud-fail number
  guard** (`agent/guards.py`): every numeral in the output must match a value in the
  input payload (after format normalization) — on mismatch the artifact is REJECTED
  and the failure printed, never published. (The proposal-agent guard, ported.)
  Normalization must cover (advisor review 2026-07-11): number-WORDS ("three
  channels", "a dozen") → digits before matching; currency/percent/thousands
  formatting variants. Matching stays EXACT after normalization — no tolerance,
  ever (a "close enough" guard is no guard). Comparative quantities that carry no
  numeral ("nearly doubled", "roughly half", "twice as expensive") assert
  computations too: the prompt bans them unless the ratio/delta itself is in FACTS —
  so the engine should compute the common deltas/ratios INTO the facts payload,
  making the useful comparisons legal instead of forbidden.
- **Recommendations rule:** deterministic code first computes the *eligible*
  recommendation candidates (the `commentary.py` flag branches + allocator outputs
  when an MMM exists), each tagged with its `recommendation_menu.md` type. The agent
  selects, orders and justifies from that menu only — it cannot invent a category or
  a budget number.
- **Labeling:** the artifact carries a front-matter stamp ("AI-drafted from computed
  facts — review before client use") and the dashboard shows it in a clearly marked
  section, off by default (`reporting.ai_commentary: false`). The deterministic
  narrative blocks remain the spine; the Overview's "no generated commentary" caption
  changes only where the AI section is enabled, to say exactly what is and isn't
  generated.

**Acceptance:** commentary renders with the stamp; corrupting one number in a test
payload makes the guard reject; recommendations only ever cite menu types; no key →
everything works with the AI section absent.

## Golden-set evals (part of A1/A2 acceptance, not optional)

The AI layer gets the same regression discipline as the 190+ deterministic tests.
`tests/test_agent_evals.py` runs 5–6 fixture scenarios (gov awareness, higher-ed
conversion near deadline, recruitment, broken-tracking with a ~4x claim ratio,
thin-data) through spec + commentary with MOCKED model responses (CI-safe; an
optional live smoke test behind the API key) and asserts:
- campaign_type and primary_tier correct per scenario;
- the guard rejects a deliberately corrupted payload;
- every recommendation ∈ the computed eligible set;
- forbidden framings absent (string checks: no CPA-grading in awareness scenarios,
  no cross-channel CPM comparisons, no unlabeled conversion numbers).

## Model routing (decided 2026-07-11)

Sonnet-class for both agents initially (`agent.model` in config, gateway default).
The spec agent is a downshift-to-Haiku candidate once evals pass on it. One call
per pipeline run → cost is pennies; do NOT engineer prompt caching for this.

## Phase A3 — later, not now

Conversational "ask the data" box (extends the lens + quick-views NL parsing), or an
MCP server exposing store/insights as tools. Park until A1/A2 earn their keep.

## Out of scope

Live per-pageload LLM calls; agents reading raw row-level data; auto-applied
recommendations; letting the agent touch `history.parquet`, the store, or any
pipeline math; generated macro/external context (curated notes only, as ever).

## Suggested kickoff for the implementing session

> Read AGENT_SYSTEM_BRIEF.md. Harry has drafted content in system/guidelines/.
> Build A1 (knowledge loader + spec agent + validation + advise.py --spec + tests,
> deterministic fallback throughout), stop for review, then A2 with the number guard.
