> **Status: design brief — implementation spec.** Shaped in Cowork 2026-07-23 from
> `docs/notes-intake-agent.md` (the CLIENTXYZ / FBI-leftover finding). Direction settled
> there: **agent proposes from the actual data, human confirms, answers become
> per-engagement config.** This brief decides the interaction; Claude Code turns it into
> a plan + implementation, same handoff pattern as `docs/plan-robust-ingestion.md`.

# Design: Intake — agent proposes, human confirms

## The invariant this feature enforces

**The form can only offer what exists in the loaded data.** Every selectable option
(outcome metric, funnel step) is a column/metric with usable rows in the current store
slice. Free text exists only for *labels, naming, and target numbers* — never for metric
or stage *identity*. That single rule makes the FBI-leftover failure unrepresentable
through this surface: you cannot confirm a framing around `application starts` if nothing
in the data backs it.

---

## 1. Trigger

**Where:** a dedicated Streamlit page, `dashboard/pages/0_Setup.py` (nav label
**"Setup"**), sorted first. Not a modal, not a wizard interrupting other pages.

**Gate logic** lives in one shared helper (new `agent/intake.py`):

```
intake_status(root) -> "confirmed" | "unconfirmed" | "invalid"
```

- `unconfirmed` — no `config/engagement.yaml` exists (see §3). First run for an
  engagement.
- `invalid` — confirmed framing exists but fails the **hard guard**: the confirmed
  primary outcome metric, or any confirmed funnel step, has zero usable rows in the
  currently loaded data. (This is the guard the notes said should ship independently —
  it does; see §3.)
- `confirmed` — engagement config exists and every framing field survives the guard.

**A new data hash alone does not retrigger intake.** On refresh, re-run the guard
against the new data: still valid → carry on silently (the whole point of durable
per-engagement config). Guard fails → status flips to `invalid`, banner appears (§4),
and the Setup page opens **pre-filled with the previous answers**, with the
now-unbacked fields flagged ("`final_offers` is no longer in the loaded data").

**Reopening later:** the Setup page is always in the nav. When status is `confirmed` it
renders the same form pre-filled with current answers — editing and re-saving rewrites
`engagement.yaml` and re-stamps its meta. Additionally, the Overview shows a small
caption-link next to the KPI label ("framing · edit") that deep-links to Setup. No
hidden state, no "you can only do this once" behavior.

## 2. Form content

Two visually distinct zones on the Setup page, in this order.

### Zone A — "What's in your data" (read-only facts, pure pandas)

Rendered from `summaries.data_summary()` output (already built — reuse, don't
recompute). Compact facts panel, no questions:

- date range, week count, currency
- channels with spend, with spend share
- outcome-like columns present, each tagged **measured** vs **platform-claimed**
  (the existing claims-vs-measured taxonomy) with coverage ("131/131 weeks")
- funnel-candidate columns that actually joined, in tier order (`config/metrics.yaml`
  taxonomy: reach → intent → outcome)
- anything *configured but absent* called out explicitly: "config references
  `conditional_offers` — not present in this data"
- top data-quality flags

This zone is the trust anchor: the user sees the agent read *their* data before it
proposes anything.

### Zone B — "Confirm the framing" (business judgments — exactly four questions)

1. **Primary outcome.** Radio over the outcome-like columns present (from Zone A),
   proposed one preselected — proposal heuristic: measured beats platform-claimed,
   then highest coverage. Plus one text input: *"What does the client call this?"* →
   `kpi_label`, prefilled with the metric's registry display name.
2. **Funnel.** The proposed funnel as an ordered checkbox list — only columns present,
   ordered by the tier taxonomy, all checked by default. Unchecking a step removes it
   from funnel rendering. No option to add a step that isn't in the data.
3. **Targets** (optional, collapsed expander, default = explicit "No target yet").
   Numeric `good`/`warn` for cost-per-primary-outcome, optional volume `goal`, and
   optional `budget: {total, flight_weeks}` for the pacing block. Skipping is a valid,
   recorded answer — the dashboard falls back to spread-derived bands, labeled as such
   (existing behavior).
4. **Naming.** `client_name`, `campaign_name` — free text, no proposal (business
   identity is not inferable from column names; don't pretend).

One primary button: **"Confirm framing"** → writes `config/engagement.yaml`, reruns.
That's the whole form. Anything else the spec agent decides (block order, watch flags,
campaign_type, default tier) stays agent territory and is *not* asked — fewest
questions that prevent the failure, not a config editor.

## 3. Persistence — proposal confirmed, with one amendment

**Confirmed:** intake answers are durable per-engagement config, surviving data
refreshes, sitting above the per-data-hash spec. Concretely — a new file,
`config/engagement.yaml` (gitignored, alongside `config.yaml`), written only by the
intake form:

```yaml
meta:
  confirmed_at: 2026-07-23T14:05:00Z
  confirmed_against_data_hash: "sha256…"   # provenance only — NOT a validity key
  source: intake_form
framing:
  kpi_metric: key_events          # metric key — guard-validated against loaded data
  kpi_label: "application starts"
  funnel_steps: [impressions, clicks, sessions, key_events]
  targets:
    cost_per_key_event: {good: 160, warn: 220}
  client_name: "…"
  campaign_name: "…"
  budget: {total: 37500000, flight_weeks: 131}   # optional
```

**Precedence**, read through one new helper the reporting layer and dashboard call
instead of touching `load_config()["reporting"]` directly:

```
resolve_framing():  config.yaml explicit keys
                    > engagement.yaml (confirmed intake)
                    > report_spec.json (hash-current spec, existing load_active_spec)
                    > deterministic defaults
```

**The amendment:** `config.yaml` stops being where framing *lives*.
`config.example.yaml` drops `kpi_label` / `client_name` / `campaign_name` / `targets` /
`budget` from the reporting block, replaced by a comment pointing at Setup. Keys still
present in a hand-edited `config.yaml` remain honored (escape hatch, existing "explicit
config wins" contract unbroken) — but the FBI mess was exactly stale hand config, so
the defaults no longer teach people to put framing there.

**The hard guard is part of `resolve_framing()`, not the UI** — it applies to the
*winning* value regardless of layer. A `kpi_metric` or funnel step with no backing in
the loaded data is **dropped loudly** (warning + `intake_status` → `invalid`), never
silently rendered. This closes the actual FBI hole even for people who never open the
Setup page, and ships first (see sequencing).

Note `engagement.yaml` never goes stale *by hash* — only by failing the guard. The
stored hash is provenance ("confirmed against the July data") for display on Setup.

## 4. Unconfirmed state — don't block the dashboard; block the shipped artifact

While status is `unconfirmed` or `invalid`:

- **Every dashboard page** shows a persistent banner (one helper in `theme.py`):
  *"Report framing not confirmed for this data — showing neutral defaults.
  → Confirm on the Setup page."* For `invalid`, the wording names the offender:
  *"Configured KPI 'application starts' isn't in the loaded data — framing reset to
  neutral defaults. → Re-confirm on the Setup page."*
- Rendering falls back to **neutral defaults, never stale judgments**: generic KPI
  label from the metric registry, no target bands (spread-derived only, labeled), no
  pacing block, funnel = present columns only, client name = `project.name`.
  Judgment-dependent Overview blocks (target scorecard bands, pacing,
  recruiting_pipeline) hide rather than render with guesses. Data-fact pages
  (Channels, Data Quality, Explore) work normally — exploration is never hostage.
- **`scripts/build_report.py` refuses** to build the client-facing HTML report while
  unconfirmed, with a clear message pointing at Setup; `--allow-unconfirmed` overrides
  and stamps a visible **DRAFT — framing unconfirmed** watermark. Blocking is for what
  ships to a client, not for looking at your own data.

## 5. No-API-key mode

The proposal is **computed deterministically in both modes** — `agent/intake.py` is
pure pandas over `data_summary()` (same category as `lens.py`'s deterministic parser).
A key changes only the *prose*, exactly the `agent.enabled` pattern:

- **No key:** Zone A renders as labeled facts; the proposal line above Zone B reads
  plainly, e.g.:
  > **Suggested primary outcome: `key_events`** — measured (GA4), data in 131/131
  > weeks. Alternative: `conversions` (platform-claimed). Suggested funnel:
  > impressions → clicks → sessions → key_events, from the columns present.
- **With key:** one guarded structured call turns the same proposal into 2–3 sentences
  of rationale ("Your most complete outcome column is `key_events`; it's
  analytics-measured rather than platform-claimed, so…") and may suggest a friendlier
  `kpi_label` prefill. **Narration is display-only: the proposed values are computed
  before the call and are never changed by it.** No key, no network, full function —
  the key upgrades writing, nothing else.

---

## Implementation seams

- **New:** `agent/intake.py` (facts + deterministic proposal + `intake_status` +
  optional narration call), `dashboard/pages/0_Setup.py`, `resolve_framing()` (in
  `utils.py` or a small `reporting/framing.py`), banner helper in `theme.py`,
  `engagement.yaml` read/write (+ `.gitignore` entry), `build_report.py` gate.
- **Touched:** dashboard pages swap direct `reporting.*` config reads for
  `resolve_framing()`; `config.example.yaml` reporting block slimmed.
- **Untouched:** spec agent generation (A1 keeps running at pipeline time and keeps
  gap-filling *below* engagement.yaml), lens, planner, ingestion.

**Sequencing:** (1) hard guard + `resolve_framing()` + banner — small, independently
shippable, closes the actual bug; (2) `agent/intake.py` facts/proposal + Setup page +
`engagement.yaml`; (3) `build_report.py` gate + optional narration call; tests
throughout.

## Acceptance criteria

- **FBI-leftover repro:** FBI-era `config.yaml` + CLIENTXYZ data loaded → no FBI
  framing anywhere on any page, `invalid` banner names the missing KPI, Setup proposes
  only from CLIENTXYZ columns. This is the regression test.
- Data refresh, same engagement, framing still backed → no re-intake, no banner.
- Confirm on Setup → banner gone everywhere, KPI label / funnel / targets / naming
  flow through, `report_spec.json` still fills unasked fields underneath.
- Unset `ANTHROPIC_API_KEY` → Setup fully functional, plainer wording.
- `build_report.py` refuses unconfirmed; `--allow-unconfirmed` watermarks.

## Out of scope

Conversational/chat intake; multi-engagement profiles or switching; changes to the A1
spec schema; asking about block order / watch flags / campaign_type (agent territory);
editing `config.yaml` programmatically.
