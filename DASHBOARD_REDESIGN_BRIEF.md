# Build brief — Dashboard redesign (hybrid narrative + drill-down)

**STATUS: APPROVED, NOT YET BUILT** (planned 2026-07-06; implement next session).
**Owner:** Harry. Treat as the spec; ask before large architectural changes.

## Design direction (decided)

Hybrid of three directions (mockups reviewed 2026-07-06):

- **Direction A is the spine** — the landing page is an editorial narrative: hero
  insight → annotated chart → woven commentary, repeated 3–4 times as a scrollable
  story. Serif headlines for insights; numbers illustrate the story, not vice versa.
- **Direction B below the fold / sub-pages** — dense interactive drill-downs: KPI
  tiles with period deltas, Plotly charts, expandable nested tables.
- **Direction C's discipline everywhere** — every chart is headed by an ACTION TITLE
  (an insight sentence: "Meta delivers applications at half LinkedIn's cost"), never
  a label ("Spend over time"). If a section can't state an insight, it doesn't belong
  on the narrative page.

Principles: presentation-first, drill-down second; the claims-vs-measured gap is the
signature honesty visual; deterministic insights only (computed from the data — top
mover, biggest efficiency gap, claim ratio, pacing); **no fabricated commentary**.

**Macro-trend commentary: DEFERRED.** Design the slot in the narrative layout
(an "External context" aside), ship it hidden behind a config flag. It activates
later via either a curated per-client notes file or a guarded LLM+web-search step
where every macro claim carries a citation. Do not fake it meanwhile.

## Audience data prerequisite (build FIRST — the redesign depends on it)

Audience performance is crucial (drives future targeting/optimization), and the
drill-down hierarchy is **channel → campaign → audience → creative**. Today the
schema and exports stop at campaign. Required work:

1. **Schema v5**: optional string columns `ad_group` (platform ad-set/ad-group name)
   and decoded `audience_type`, `audience_detail`, `creative`, `creative_format`
   (default "" — absent for campaign-level sources). Dedup grain gains `ad_group`.
2. **Ad-level sample exports**: extend `scripts/generate_sample_exports.py` with
   ad-set/ad-level files (Meta ad-set export, LinkedIn creative performance, Google
   ad-group report) whose names follow the naming-generator grammar
   (e.g. `PROSPECT_LAL-1PCT_FEED` / `BRANDHERO_VID_9x16`) **plus deliberately
   non-conforming names** (~15%) to exercise the unparsed bucket. Extend
   `_ground_truth.json` with per-audience truth.
3. **Naming decode module** (`src/advanced_reporting/naming/decode.py` or
   `ingestion/naming_decode.py`): parse ad-set/ad names back into audience/creative
   fields using the generator's scheme (this is the naming tool's core promise —
   names are decodable). Non-conforming names land in an explicit
   `audience_type="(unparsed)"` bucket — never guessed. The unparsed-rate is itself
   a reported metric (and the sales pitch for adopting the naming convention).
4. **Readers**: extend `ingestion/exports.py` for the ad-level formats; decode at
   ingest so the store carries the parsed fields.

**Honesty note for the audience pages:** GA4 key events are campaign-level —
audience/creative-level conversions are platform-claimed only. Audience views must
label their conversion columns "platform-claimed" and show cost-per-claimed-conv,
never implying GA4 verification below campaign grain.

## Build phases (in order)

- **R0 — audience data work** (items 1–4 above). Acceptance: ad-level fixtures
  ingest via `--inbox`; decoded fields in history.parquet; unparsed bucket + rate
  visible; tests for decode round-trip (generator names → decode → same fields)
  and non-conforming fallback.
- **R1 — theme + chart standard.** `.streamlit/config.toml` theme (palette/fonts,
  light mode first); `dashboard/theme.py` with design tokens + one `plotly_chart()`
  helper enforcing house style (fonts, gridlines, hover, annotation style, number
  formats). Every subsequent chart goes through it. Plotly replaces all st.line_chart
  / st.bar_chart / raw dataframes.
- **R2 — executive narrative page.** 3–4 deterministic insight blocks computed from
  the weekly tables: headline KPI + trend with annotations; claims-vs-measured
  visual; cost-per-key-event by channel; pacing vs budget. Serif action titles,
  woven commentary paragraphs (deterministic template over computed facts), macro
  slot hidden. Sign-off checkpoint: review this page before building further.
- **R3 — drill-down pages.** Channel explorer (per-channel trends, efficiency
  scatter, campaign table) + the nested table: channel → campaign → audience →
  creative with spend / clicks / claimed conv / (campaign level only) key events +
  cost columns, expandable rows, CSV download.
- **R4 — audience performance page.** The optimization view: audience_type ×
  audience_detail cost-per-claimed-conv ranking, spend share vs performance share,
  creative format comparison within audience, trend per top audience, unparsed-rate
  callout. Everything labeled platform-claimed per the honesty note.

Multipage layout: `dashboard/app.py` becomes the narrative Overview;
`dashboard/pages/` gains Channels, Audiences, Data quality (existing DQ content
moves there). Keep `--sources` scoping and no-MMM mode working throughout; the MMM
sections reappear on their own page automatically when a KPI series exists.

## Out of scope (this build)

Real macro-trend content; LLM-written commentary; the generated single-file HTML
client report (deliberate follow-up); React/Next front-end; MMM/plan pages beyond
what already exists.

## Suggested kickoff for the implementing session

> Read DASHBOARD_REDESIGN_BRIEF.md. Execute R0 first (schema v5 + ad-level fixtures
> + naming decode + tests), then R1, then stop after R2 for design sign-off on the
> narrative page before R3/R4.
