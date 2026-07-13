# Build brief — "FBI Talent Acquisition" synthetic engagement (MMM + Meridian + full UI)

**STATUS: P0–P3 COMPLETE (2026-07-13)** — scenario+grammar+spike (P0), DGP+emitters+ground-truth (P1), ingest+store+descriptive pipeline (P2), baseline MMM on applications with count-target verdicts (P3); `pytest -q` green (303 passed, 1 skipped). Dashboard ties out on the single FBI engagement ($37.5M, 10 geos, 131 wks); DQ anomalies cluster only in designed periods (Jan post-holiday ramp + May National Recruiting Week bursts + LinkedIn dark weeks). **P3 recovery:** rank corr ≈0.76, held-out R² 0.95, baseline recovered ≈83% (believable paid/baseline split), the collinear pair (meta/youtube) + saturation (google) + tiny audio behave as designed (wide/unproven). The baseline engine still over-credits paid modestly — the documented motivation for **Meridian (P4)**. Count-target layer (target_kind: count → cost-per-incremental-application verdicts good≤$400/warn≤$650, Incrementality page + commentary + validation.md formatting, "media buys applications; it cannot pass a polygraph") built + tested. **P4 (Meridian), P5 (UI finish) pending.** NB: fixed a silent geo×week flatten-scramble in the DGP; DGP tuned for MMM identifiability (per-channel rhythms, informative unemployment control, holiday quiet weeks) — see memory notes.
**MERIDIAN SPIKE (P0): SUCCESS on Windows CPU.** `uv pip install google-meridian` → `google-meridian==1.7.0` (TensorFlow 2.20.0, tfp-nightly 0.26.0.dev, keras 3.15) installs and `import meridian` works in the project venv (Python 3.12.13). No WSL2/Colab fallback needed. Note: the install pinned numpy 2.5.1→2.3.5, pandas 3.0.3→2.3.3, matplotlib 3.11.0→3.10.9 in the shared venv; full suite re-run stayed green, so the downgrade is benign. P4 can attempt a real geo-level fit locally; CI must NOT install Meridian (heavy) — keep it manual/local per the `--mini` rule.
**(originally: APPROVED, NOT YET BUILT — planned 2026-07-13 on Fable; execute on cheaper models.)**
**Owner:** Harry. Treat as the spec; ask before deviating from a DECIDED item.
**Scope decisions (settled 2026-07-13):** 2.5yr × 10 regions · ingestion via platform export
files · MMM target = submitted applications (count) · task ends at **P5** (UI finish).
The planner full-circle demo (fitted MMM → CampaignPlan → trafficking sheet) is **out of
scope** — a follow-up task.
**Amended 2026-07-13 (Harry):** (1) all emitted data lands in **`data/MMM Data/`** as a
self-contained dataset folder; (2) applications happen on the **FBI's own careers portal**
(fbijobs-style), NOT USAJOBS; (3) the dataset carries the **post-submission applicant
pipeline** at major-phase grain only (6 phases, no sub-tasks) — see "Applicant pipeline".

## Goal

One clean, engagement-scoped synthetic dataset — a fictional FBI recruiting program — big
and structured enough to:

1. **Fit and validate BOTH MMM engines** (baseline + Google Meridian) against a known
   ground-truth DGP, including finally unguarding `meridian_engine.py` per the CLAUDE.md
   rule (only after a real Meridian run checks out).
2. **Fully light up every dashboard surface**: all three KPI-pyramid tiers, audiences →
   creatives drill-down, pacing-vs-plan, client-target gauges, the Incrementality page,
   AI spec/commentary, and the client HTML report.
3. **Prove the naming convention at scale**: generated names → trafficked → decoded at
   ingest, with a realistic ~12% unparsed tail.

## The fiction (the scenario bible — every number obeys it)

Continuous FY24–FY26 FBI recruiting program. **~$15M/yr paid media, ~$37.5M total.**
Flight: **Mon 2024-01-01 → Sun 2026-07-05 = exactly 131 complete weeks** (no partial edge
weeks; the partial-week handling is already proven elsewhere).

- **Career paths** (campaign initiative dimension): **SA — Special Agent (~45% of spend,
  the hero)**, INTEL (Intelligence Analyst), CYBER (Cyber/STEM), LING (Linguists),
  PROF (Professional Staff / Forensic Accountants).
- **Paid channels (8):** google_search, youtube, meta, linkedin, display (programmatic),
  ctv, audio, jobboards. **Deliberately NO TikTok** (federal device ban — realism).
- **Non-paid rows (4):** organic_search, direct, email (owned nurture, $0 spend),
  social_organic.
- **Funnel:** impressions → clicks → GA4 sessions on the **FBI careers portal**
  (fbijobs-style — the Bureau's own site, NOT USAJOBS; GA4 property on the portal) →
  engaged sessions → **application starts** (`key_events`, GA4-measured at campaign
  grain) → **submitted applications** (weekly CRM matchback = the MMM target). This
  dataset ships the `unlock_mmm` recommendation *fulfilled*.
- **Geos (10 field-office regions, `US-*` codes):** NE, MA (heavy — HQ gravity), SE, GL,
  MW, SC, MTN, PNW, PSW, NCA. Each carries a population (rough census weights) — Meridian
  needs it.
- **Analytics vendor:** GA4 (the mid-funnel tier + UTM joins are already wired for it).
  Labels stay kpi_label-driven/vendor-neutral as built.

## Calibration targets (aim here; exact values live in the scenario YAML)

| Channel | Spend share | Grain / reader | CPM / CPC | CTR | Claim ratio | Adstock decay | True ROI posture |
|---|---|---|---|---|---|---|---|
| google_search | 22% | ad-group · `read_google_adgroup_export` | ~$4.50 CPC | 3.5% | ~2.2x | fast (0.2) | high avg, **deep in Hill saturation** (stress d) |
| meta | 18% | ad-set · `read_meta_export` | $9 CPM | 1.1% | ~3.2x | 0.5 | good |
| youtube | 15% | ad-group · google reader (video campaigns) | $14 CPM | 0.4% | ~3.5x | slow (0.65) | mid |
| linkedin | 12% | creative · `read_linkedin_creative_export` | $32 CPM | 0.55% | ~2.6x | 0.4 | strong for INTEL/CYBER/PROF |
| ctv | 12% | campaign · generic csv mapping | $38 CPM | ~0 clicks | **extreme** (stress g) | slowest (0.75) | mid (halo) |
| display | 10% | campaign · generic csv mapping | $3.50 CPM | 0.12% | ~4.5x | 0.35 | **weak — the cut candidate** |
| jobboards | 7% | campaign · generic csv mapping | ~$1.80 CPC | 8% | ~1.4x | fast | solid; near-saturated for PROF |
| audio | 4% | campaign · generic csv mapping | $22 CPM | ~0 | n/a (promo-code trickle) | 0.6 | **tiny spend → "unproven"** (stress e) |

**Blended calibration:** cost per application start ≈ **$150–200**; start → submitted
≈ **42%**; ≈ 88k starts/yr, ≈ 37k submitted applications/yr; paid drives **~55–65% of
submitted apps** (the rest is baseline/organic demand). Blended claim ratio ≈ 2.8–3.2x.
SA converts hardest (costliest), PROF cheapest.

## Applicant pipeline (post-submission — NEW, amended 2026-07-13)

The applicant journey continues through the Bureau's gate process after submission. We
track the **6 major phases only** (per the portal's tracker; sub-tasks like "schedule
PFT" are deliberately NOT modeled — majors keep the DGP and UI tractable):

`initial_screening → meet_greet → testing → conditional_offer →
background_investigation → final_offer`

**Hard rule — MMM boundary:** the MMM target remains **submitted applications**. The
pipeline stages are selection-driven and lag media by months; they are a REPORTING
layer, never a modeling target. The UI must say this in the product's honesty voice
("media buys applications; it cannot pass a polygraph").

**Cohort DGP:** the generator models application-week cohorts flowing through the gates
with path-specific pass rates and lags, then emits **calendar-week counts** (what an ATS
export looks like). Recent cohorts are **right-censored** — realistic, and the UI
annotates it ("pipeline still maturing; final offers completing now stem from
applications ~9–12 months ago").

Special Agent calibration (other paths gentler — fewer washouts, ~half the lags,
cumulative 15–25%):

| Phase (entering) | Pass rate from prior | Lag from submission |
|---|---|---|
| initial_screening (incl. Phase I test) | 40% | 2–4 wks |
| meet_greet | 75% | 4–8 wks |
| testing (PFT + Phase II) | 45% | 8–16 wks |
| conditional_offer | 80% | 16–24 wks |
| background_investigation (poly + BI) | 75% | 24–40 wks |
| final_offer (accept → BFTC) | 90% | 40–60 wks |

Cumulative SA ≈ **7%** — with ~40% of ~37k submissions/yr being SA, that's **≈1,100 SA
entering BFTC per year**, matching Quantico's real-world throughput. That's the anchor;
keep it.

**Emission:** `crm_pipeline_stages.csv` in the dataset folder — weekly counts by
`week × geo × initiative × stage` (+ a `channel` column via last-touch CRM attribution,
emitted but only surfaced in the stretch item below). ~40k rows; trivial.

**UI (right-sized — do not gold-plate):**
- `metrics.yaml`: outcome-tier VOLUME metrics `conditional_offers`, `final_offers`
  (counts only — no in-window cost-per-stage efficiency metrics; lagged denominators
  would mislead).
- ONE new deterministic insight block, `recruiting_pipeline`: 6-stage funnel with
  stage-to-stage pass-through, the censoring annotation, and a woven narrative.
  Registering it means extending `agent/validate.py BLOCK_CATALOG` + the renderer dicts
  in `app.py` and `html_report.py` (the existing assert keeps them in sync).
- **P5 stretch (only if P1–P5 land cleanly):** applicant *quality* by channel —
  screening-survival rate by last-touch channel ("cost per qualified applicant"),
  explicitly labeled last-touch CRM attribution, never causal.

## Baked-in MMM stress tests (this is what makes it a *test*)

- **(a) Collinear pair:** meta + youtube share one pulse calendar → wide intervals *by
  design* (the commentary must hedge — it's built to).
- **(b) Dark channel:** linkedin paused 6 weeks (Oct–Nov 2025 "budget freeze") →
  identifiability from on/off.
- **(c) Burst:** National Recruiting Week, ~2.5× spend on search/meta/youtube for 2
  weeks, May 2025 and May 2026. (DQ spend-spike flags will fire — that's a *designed*
  anomaly, note it on the DQ page expectations.)
- **(d) Saturation:** google_search brand deep into the Hill plateau — high average ROI,
  poor marginal ROI.
- **(e) Unproven:** audio's spend is too small to identify → interval spans break-even.
- **(f) Geo lever:** ctv launches in 4 regions Jul 2024, all 10 by Mar 2025 — the
  cross-geo variation Meridian exists for.
- **(g) Zero-click honesty case:** ctv claims thousands of conversions; GA4 can verify
  almost none (no UTM path) → an extreme claim ratio the UI must render gracefully and
  MMM then adjudicates. The product's thesis in one channel.

**Seasonality/controls:** grad-season lift (May–Jun), new-year career-switch spike (Jan),
`unemployment_index` (slow-moving, positively correlated with applications) and
`news_spike_flag` (a few 1–2wk shocks) as `control_cols`. Controls are emitted in the
CRM KPI file alongside applications.

## Architecture (DECIDED)

1. **Scenario-driven DGP — additive, never refactor the old generator.** New module
   `src/advanced_reporting/ingestion/scenario_dgp.py` + scenario spec
   `config/scenarios/fbi_recruitment.yaml` (channels, budget shares, weekly flighting,
   per-channel adstock/Hill params, true ROI as incremental applications/$, claim-ratio
   params, CPM/CTR bands, funnel rates, geo populations + multipliers + staggered
   launches, seasonality, controls, unparsed-name tail, seed). The existing
   `generate_sample_data.py` / `SyntheticSource` DGP stays **untouched** (tests depend
   on it). Ground truth → `outputs/ground_truth.json` (per-channel true contribution +
   ROI + baseline share). **Accounting identity enforced by test: baseline + Σ channel
   contributions = KPI, exactly.**
2. **Emission = realistic platform export files** written by
   `scripts/generate_fbi_campaign.py` into **`data/MMM Data/`** (amended location — a
   self-contained, gitignored dataset folder: all platform exports + GA4 + CRM files +
   `ground_truth.json` copy + a README describing the scenario; ingest reads it via
   `ingest.py --inbox "data/MMM Data" --reset`):
   - Google ad-group export covers **search AND youtube** (video campaigns are Google
     Ads); Meta ad-set export; LinkedIn creative export; GA4 export — all via the
     existing `exports.py` readers.
   - display / ctv / audio / jobboards at **campaign grain** via generic csv mappings —
     `config/mappings.yaml` entries only, **no new readers**.
   - Ad-level exports carry the **geo breakdown** (platforms support geo segments;
     synthetic liberty keeps `modeling_table_geo` complete).
   - One grain per channel (the store supersedes campaign rows covered by ad-level —
     never ingest both for the same channel).
   - CRM: `business_kpi_weekly.csv` gains a **geo column** (+ controls); national table
     derived by aggregation (small `build_modeling_table` extension). It is emitted into
     `data/MMM Data/` — P2 makes the pipeline's KPI path configurable (or copies it to
     `data/raw/`; executor picks the smallest clean change). Plus
     `crm_pipeline_stages.csv` (see "Applicant pipeline").
3. **Fresh engagement:** ingest with `ingest.py --inbox "data/MMM Data" --reset`
   (archives the old mixed store). `config data.sources: null` — ONE engagement,
   every page ties out. This permanently kills the Audiences-page cross-dataset
   incoherence found in the 2026-07 UI review.
4. **Naming loop — dogfood the grammar.**
   - Extend `planner_rails.yaml` vocab: audiences PROSPECT {LAL-1PCT, INT-LAW, VET-MIL,
     STEM-GRAD, LANG-ARA, CAMPUS}, RETARGET {SITE-90D, ENGAGE-30D, ABANDON-APP};
     creatives {HERO_VID_16x9, DAYINLIFE_VID_9x16, MISSION_STAT_1x1, TESTIM-AGENT_VID_16x9}.
   - **Career-path grammar (P0, DECIDED design):** add `initiative` as an **optional
     TRAILING campaign segment** — `US_META_CONVERT_PROSPECT_SA`. Decode reads segments
     0–3 exactly as today; segment 4, when present, = initiative. **Backward compatible:
     all existing 4-segment names must still decode (test it).** Generator + decode +
     round-trip tests.
   - **Unparsed tail:** ~12% of ad-level spend across ~6 legacy names concentrated in
     meta + search (e.g. `SA_recruit_2024_final_v2`, `Advantage+ broad (test)`).
5. **MMM target = submitted applications (count).** `BaseMMM.fit(target=...)` already
   takes any column. The real work is display-layer: introduce
   `modeling.target_kind: count` (default `currency` — nothing else changes) and make
   the Incrementality page / mmm_view / commentary format the target as a count and
   report **cost per incremental application** per channel.
   **⚠ Verdict logic:** the current profitable/unprofitable verdicts test ROI intervals
   against **1.0** — meaningless for a count target (apps/$ ≈ 0.005). For count targets,
   grade each channel's **cost per incremental application interval** against a client
   band (`modeling.cost_per_outcome_target: {good: 400, warn: 650}` in config) —
   interval entirely better than `good` = strong, entirely worse than `warn` = cut
   candidate, spanning = unproven. Provenance labels as built ("client target").
6. **Meridian (P4 — spike its environment FIRST, during P0/P1):** try
   `google-meridian` on Windows CPU in the venv; fallback **WSL2**; last resort a Colab
   run exporting fitted artifacts. Only after a real fit validates: implement the
   `Analyzer → MMMResult` mapping and **unguard** `meridian_engine.py`. Inputs: geo ×
   weekly media + geo KPI + population. Deliverable: side-by-side validation report —
   **truth vs baseline vs Meridian** (ROI rank order, contribution shares, interval
   coverage, stress cases (a)–(g) behaving as designed).
7. **Scale budget:** ~131 weeks × 10 geos; ad-level grain on 4 channels (~3 ad groups ×
   5–6 campaigns each), campaign grain on 4; ~60–70% flighting occupancy → **target
   ≈ 0.7–1.0M store rows** (parquet ~40–80MB). Knobs in the scenario if the dashboard
   drags. **`--mini` preset (16 weeks × 3 geos, same scenario shape) for tests — CI
   never runs the full generation or Meridian.**
8. **UI finish config (P5):** `kpi_label: "application starts"`;
   `reporting.budget {total, flight_weeks}` (pacing-vs-plan lights up);
   `reporting.targets.cost_per_key_event {good: 160, warn: 220}` (client-target
   provenance); client/campaign cover names ("Federal Bureau of Investigation — Talent
   Acquisition" · "Omnichannel Recruiting FY24–26", clearly fictional);
   `campaign_goals.yaml` mappings for the objective segments; CHANNEL_LABELS +
   CHANNEL_COLORS identity entries for ctv/audio/jobboards/social_organic;
   `modeling.channel_spend_cols` + `control_cols` updated. Then
   `advise.py --spec --commentary` (key available; bump commentary `LOGIC_VERSION` if
   any computed-block wording changed), `build_report`, and **re-run
   UI_REVIEW_PROMPT.md against the finished product**.

## Build phases (in order — each gated; `pytest -q` green at every gate)

- **P0 — scenario + grammar + env spike.** Commit this brief; write
  `config/scenarios/fbi_recruitment.yaml`; vocab extensions; the `initiative` segment
  (generator + decode + backward-compat round-trip tests); **Meridian install spike in
  parallel** (venv → WSL2 fallback; record the outcome in the brief's status line).
  *Accept:* scenario validates against a schema check; old 4-segment names still decode;
  `import meridian` (or documented fallback) works.
- **P1 — DGP engine + export emitters + ground truth.** `scenario_dgp.py`,
  `generate_fbi_campaign.py`, ground_truth.json — all emitted into `data/MMM Data/`.
  Includes the applicant-pipeline cohort model + `crm_pipeline_stages.csv`. Tests:
  seeded determinism; accounting identity exact; emitted exports pass `schema.validate`
  through their readers; naming round-trip incl. initiative; unparsed rate ≈ configured;
  pipeline cohorts monotone non-increasing with pass rates ≈ configured and censoring
  present in the final year; `--mini` generates in <5s.
  *Accept:* realism sanity table printed (per-channel CPM/CTR/cost-per-start inside the
  calibration bands above; SA cumulative pipeline ≈ 7% → ≈1,100 BFTC/yr).
- **P2 — ingest + store + descriptive pipeline.** mappings.yaml entries; `--inbox
  --reset` ingest; geo-KPI merge into `build_modeling_table`; pipeline e2e descriptive.
  *Accept:* all six dashboard pages tie out on the single engagement (spend/starts equal
  everywhere); Audiences fully populated from decoded names; DQ shows only the
  *designed* anomalies (burst-week spend spikes).
- **P3 — baseline MMM on applications.** `target_kind: count` + cost-per-incremental-
  application verdicts (replaces ROI-vs-1.0 for count targets) + Incrementality/
  commentary formatting. Fit → `validation.md` vs ground truth.
  *Accept:* ROI rank order recovered; intervals cover truth for the ≥ mid-spend
  channels; stress (d) shows saturation in response curves; (e) unproven.
- **P4 — Meridian.** Analyzer → MMMResult mapping; **unguard**; geo-level fit.
  *Accept:* side-by-side truth/baseline/Meridian report committed (table + charts);
  stress cases (a),(b),(f),(g) behave as designed; `modeling.engine: meridian` renders
  the Incrementality page end-to-end.
- **P5 — UI finish + review.** Config block above; colors/labels; the
  `recruiting_pipeline` insight block (+ BLOCK_CATALOG/renderers sync) and the
  outcome-tier pipeline volume metrics; spec + commentary + client report regenerated;
  screenshot pass of all pages; **re-run UI_REVIEW_PROMPT.md** and fix quick wins it
  surfaces. Channel-quality (last-touch) stretch only if everything else landed.
  *Accept:* "finished UI" — every page populated, totals reconcile, Incrementality
  rendered from a real Meridian (or baseline, if Meridian env ultimately blocked —
  say so loudly) result.

## Risks / mitigations

- **Meridian on Windows (TF dependency)** — the biggest unknown; spiked in P0, WSL2
  fallback, Colab last resort. If truly blocked, P4 delivers baseline-only + a written
  env report; do NOT unguard the engine without a real run.
- **Collinear pair makes baseline look bad** — intended; the honest-hedging commentary
  and Meridian's priors are the story.
- **Row count vs dashboard latency** — knobs: geos, ad groups, occupancy; `st.cache_data`
  already on the loaders.
- **Verdict semantics for a count target** — solved by design decision 5; do not ship
  ROI-vs-1.0 on applications.
- **CI weight** — `--mini` everywhere in tests; full generation + MCMC are manual/local.

## Out of scope (this build)

Planner full-circle (P6 — future task); real platform connectors; TikTok; a schema-level
career-path column (initiative lives in the campaign name only); demographic breakdowns;
macro-trend commentary content; pipeline **sub-task** gates (majors only); cohort-grain
pipeline UI (calendar-week counts only); any MMM on post-submission stages; USAJOBS
(applications live on the Bureau's own portal).

## Suggested kickoff for the implementing session

> Read FBI_CAMPAIGN_DATA_BRIEF.md and CLAUDE.md. Execute P0 first (scenario YAML +
> initiative grammar + Meridian env spike), report the spike outcome, then P1 and P2.
> Stop after P2 for a dashboard sanity check before P3/P4. pytest -q must be green at
> every phase gate.
