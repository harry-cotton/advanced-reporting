# CLAUDE.md — Advanced Reporting

Project context for Claude Code. This file is auto-loaded each session, so it's kept
high-signal. Owner: Harry.

## What this project is

An end-to-end reporting tool for advertising & marketing campaigns. It **cleanses and
organizes** granular campaign data, feeds it into **Media Mix Modeling (MMM)**, then
**compiles, visualizes, and writes commentary** on the results — plus a **goal-aware dashboard**
(KPI pyramid + free-text report lens) over standard metrics. The long-term goal is automation,
live data connectors, and agentic reporting.

## How we work (the workflow)

- **Brainstorming, research, and strategy** happen in Claude **Cowork** (a separate tool).
  Direction and ideas are decided there.
- **Implementation, testing, git, and task tracking** happen here in **Claude Code**.
- When Harry pastes a brief or idea from Cowork, treat it as the spec. Ask before large
  architectural changes; prefer Plan mode for anything non-trivial.

## Architecture — seven layers (`src/advanced_reporting/`)

**Data flow:** `scripts/ingest.py` (extract → durable store) → `scripts/run_pipeline.py`
(clean → model → report). Extraction is **decoupled** from modeling: pulls accumulate in a
store the pipeline reads from, so history grows over time. A second, forward path —
`scripts/plan_campaign.py` — consumes the same store + a fitted `MMMResult` to produce a
`CampaignPlan` that feeds the naming generator.

1. **`ingestion/`** — extraction layer, architected so automating real platforms is just
   "fill in the API call":
   - `base.py` — the `DataSource` contract: `fetch(start, end)` returns the canonical schema;
     the base also gives every connector `require_credentials(*env_keys)` (reads `.env`, never
     hardcoded → `MissingCredentialsError`) and `with_retries(...)`.
   - `schema.py` — canonical **daily** long schema (one row per
     `date × channel × campaign × geo`: spend, impressions, clicks, conversions,
     platform_revenue, currency, plus **optional** mid-funnel / web-analytics columns —
     sessions, engaged_sessions, page_views, video_views, avg_engagement_seconds (NaN for ad
     sources)). `to_canonical()` / `normalize()` / `validate()`.
   - `synthetic.py` — `SyntheticSource`, the **active** ad source; wraps the known
     ground-truth DGP shared with `scripts/generate_sample_data.py` (single source of truth, so
     the on-disk CSVs and the live source never drift). Emits across a few geos, plus synthetic
     engagement (sessions / engaged / page & video views) so the mid-funnel tier is populated.
   - `connectors.py` — `GoogleAdsSource / MetaSource / TikTokSource / LinkedInSource / GA4Source`
     skeletons; `fetch()` raises `NotImplementedError` with exact wiring instructions. **GA4** is
     the web-analytics source for the mid-funnel tier — no spend, joined onto ad data via UTM
     source/medium/campaign (`ga4` map in `config/mappings.yaml`). `supermetrics.py` is an
     alternative single-API multi-platform stub.
   - `factory.py` — `get_source(name)` (mirrors `mmm/factory.py`, lazy imports);
     `csv_source.py` reads local CSV exports via the schema/mappings.
   - `store.py` — durable raw store: `write_pull()` writes immutable, date-stamped CSVs under
     `data/raw/<source>/`; `consolidate()` merges **all** pulls into
     `data/processed/history.parquet` (grain = `KEY_COLS` incl. ids + `source`; same-key rows
     WITHIN one pull are **summed** — unmodeled Age/Gender/Device breakdowns aggregate away,
     never keep-last data loss — while cross-pull dups keep latest = restatements; non-daily
     pulls (>10% unparseable dates) are refused loudly; incremental + idempotent) and writes
     a manifest. Robust messy-export ingestion (column synonyms, missing-optional tolerance,
     config-driven generic reader for unknown platforms): `docs/plan-robust-ingestion.md`.
2. **`transform/clean.py`** — reads `history.parquet`, **reuses `schema.py`** for validation
   (never re-implements the contract), standardizes channels (`config/mappings.yaml`), fixes
   negatives/missing/dupes, fills calendar gaps per channel×geo, then emits the national
   **weekly modeling table** plus a **geo×weekly** table (`modeling_table_geo.csv`). Also
   produces a structured **data-quality report** (`outputs/data_quality.md`): missingness,
   coverage gaps, spend-spike & zero-spend anomalies, currency consistency.
3. **`mmm/`** — `base.py` (`BaseMMM` + `MMMResult`), `transforms.py` (adstock / Hill
   saturation), `baseline.py` (works), `meridian_engine.py` (target engine, guarded),
   `factory.py` (`get_engine`).
4. **`reporting/`** — `charts.py` (matplotlib); `commentary.py` (uncertainty-aware, hedged);
   `html_report.py` (**single-file client report** — self-contained HTML, base64 matplotlib
   charts, theme.py tokens; reuses the insights payloads + spec framing + stamped AI
   commentary when hash-current; NO model calls at build time; `scripts/build_report.py`);
   `metrics.py` (tiered **KPI-pyramid** taxonomy — reach/intent/outcome — from `config/metrics.yaml`,
   campaign-goal tagging from `config/campaign_goals.yaml`, plus funnel + value-format helpers);
   `lens.py` (**free-text report lens**: NL intent → `ReportSpec` → deterministic metrics + a
   tailored narrative; deterministic keyword parser is the default, guarded LLM path optional);
   `framing.py` (**intake/framing resolver** — `resolve_framing()`: per-field cascade
   `config.yaml` > `config/engagement.yaml` > report spec > neutral defaults, data guard applied
   to the winning value; status `confirmed`/`unconfirmed`/`invalid`; lenient default =
   guard-passing hand config confirms, `reporting.intake_mode: strict` requires Setup; stored
   data hash is provenance only — refreshes never retrigger intake, guard failures do. Dashboard
   shows `theme.intake_banner` + hides judgment blocks while not confirmed. **Client-report
   gate:** build refuses while unconfirmed/invalid (`UnconfirmedFramingError` → Setup pointer);
   `--allow-unconfirmed` builds a DRAFT-watermarked report from neutral values. Design sources:
   `docs/design-intake-agent.md` + `docs/notes-intake-agent.md`).
5. **`dashboard/`** — Streamlit multipage app. `app.py` is the editorial **narrative
   Overview** (executive tile row + 4 deterministic insight blocks from `insights.py`;
   serif action titles; the claims-vs-measured honesty visual). Sub-pages (filename-order
   nav, so Setup is first): `0_Setup` (**intake** — the agent proposes a framing from the
   LOADED data, the human confirms → `config/engagement.yaml`; the form offers only
   data-backed options, optional LLM narration is display-only), `1_Channels`
   (trends, efficiency scatter, nested channel→campaign→audience→creative table),
   `2_Audiences` (cost-per-claimed rankings — everything below campaign grain is labeled
   platform-claimed; unparsed-name callout), `3_Data_Quality`, `4_Explore` (KPI pyramid +
   goal lens + free-text lens), `5_Results` (the MMM page — engine-agnostic render of the
   persisted `MMMResult`, populated only when run artifacts exist), `6_Geography` (regional
   heat map + over/under-index vs population; descriptive, never causal). `theme.py` holds
   the design tokens + the one
   `plotly_chart()` house-style helper every chart goes through; `drilldown.py` the
   pure-pandas aggregations (its `ad_group_table` structurally carries NO key_events —
   GA4 measures at campaign grain only).
6. **`agent/`** — the agent system (AGENT_SYSTEM_BRIEF.md): agents CONFIGURE, the engine
   COMPUTES, agents NARRATE. **A1 (built):** `knowledge.py` (system/ loaders),
   `summaries.py` (compact computed summaries + the data hash — never raw rows),
   `spec_agent.py` (one structured call at pipeline time → `outputs/report_spec.json`,
   cached per data-hash), `validate.py` (clip to metric registry / block catalog /
   tier enums — planner-rails style). `scripts/advise.py --spec` runs it; the dashboard
   gap-fills from the spec (kpi_label, targets, default tier, block order) with explicit
   config always winning; no key / no spec → behavior unchanged. `agent.enabled: false`
   in config is the per-engagement data-egress opt-out. **A2 (built):**
   `recommendations.py` (deterministic eligibility per recommendation_menu.md — the agent
   can never invent a type or number; `rebalance_channel_budget` never eligible until the
   allocator is wired into reporting), `guards.py` (loud-fail number guard: every numeral
   incl. number-words must exist in FACTS post-normalization, exact match, multiplier
   words rejected), `commentary_agent.py` (FACTS from the insights payloads + tier
   scorecard + MMM summary; structured output with rec types enum-pinned to the eligible
   set; guard-rejected drafts are never written) → `outputs/commentary_ai.md` (stamped,
   hash-keyed). Dashboard shows it only when `reporting.ai_commentary: true` (off by
   default). Golden-set evals in `tests/test_agent_evals.py` (mocked, CI-safe) + a live
   smoke test that activates when a key is present. **Intake (built):** `intake.py` —
   deterministic facts + framing proposal from the store (outcome coverage, funnel
   candidates); feeds the Setup page; an API key only upgrades the proposal's prose
   (`narrate_proposal`), never its values. A3 parked — see the brief.
7. **`planner/`** — turns *goals + rails* into a validated **`CampaignPlan`** that feeds the
   naming generator (Plan rows → names + UTMs). Same spine as `lens.py`: deterministic default
   + a **guarded LLM path** (one structured call — model set in rails `llm.model`, Sonnet-class default — when a key is
   set). The **LLM only selects/justifies, clipped to the rails** (it can't invent options or
   budgets); **all numbers come from the deterministic `allocator`** (marginal-return
   water-filling against the MMM response curves under the rails' min/max; rules fallback when
   no MMM). `schema.py` (`CampaignPlan` + `to_plan_rows()` = the generator's exact `PLAN_COLS`),
   `evidence.py` (CPA/ROAS/CVR from the store, incremental return + saturation from `MMMResult`,
   platform-forecast stubs), `allocator.py`, `planner.py` (`plan_campaign(goals, rails)` + an LLM
   trace with token cost), `validate.py` (rails enforcement — never trusts the LLM), `factory.py`
   (`get_planner`), `naming_bridge.py` (writes the generator's Plan xlsx). Rails =
   `config/planner_rails.yaml` (committed hard constraints). The **`naming/`** generator (repo
   root, dep `openpyxl`) is the encode tool the planner feeds.

The business KPI / model target (`revenue`) is a separate weekly file
(`data/raw/business_kpi_weekly.csv`), merged in `build_modeling_table`; the canonical history
holds the granular media data.

## Key decisions (don't reverse without discussion)

- **Target MMM engine = Google Meridian** (Bayesian; open-source pip library; runs anywhere
  incl. AWS — no GCP lock-in). The **`baseline`** engine (geometric adstock + Hill saturation
  + ridge-regularized non-negative regression + bootstrap CIs) is the validated default and
  stand-in. Engines are pluggable via `mmm/factory.py`; select with `modeling.engine` in config.
- `meridian_engine.py` is **VALIDATED + unguarded** (2026-07-13, google-meridian 1.7.0, FBI
  recruiting dataset): the `Analyzer → MMMResult` mapping is implemented and real geo-level
  MCMC fits produce sensible results (rank corr ≈0.90, all channels within 2x of truth,
  held-out R² ≈1.0 — beating the national baseline engine). Two count-KPI-specific choices,
  both required: `paid_media_prior_type="contribution"` (the default ROI prior is calibrated
  for revenue and pins a count-KPI's tiny apps/$ ROI near 1); national time-only controls are
  DROPPED (Meridian rejects controls that don't vary across geos — its per-time knots absorb
  them). The mapping still fail-loud raises on an unrecognised posterior shape (version guard),
  and Meridian MCMC is manual/local only — **CI never fits Meridian**. Select with
  `modeling.engine: meridian`; `scripts/compare_engines.py` writes the truth/baseline/Meridian
  side-by-side.
- **Data layer (Phase 2, built):** canonical **daily** schema; `DataSource.fetch(start, end)`
  is the extraction contract; the **synthetic source is active** and real-platform connectors
  are skeletons. The **store (`history.parquet`) is the source of truth** the pipeline reads —
  not a live fetch — so granular history accumulates (the platforms now delete it: Google caps
  granular data at 37 months, Meta ~13, TikTok/LinkedIn ~12).
- **Stay source- and engine-agnostic:** new sources go behind `DataSource` + the canonical
  schema; new engines behind `BaseMMM` / `MMMResult`. Transform / model / report never know the
  platform or the engine.
- **Commentary must stay uncertainty-aware**: always report 90% intervals, hedge causal
  language, and flag channels the model can't identify. Never over-claim causation.
- **Planner = thin "LLM-selects, optimizer-decides" layer** (`planner/`, built): no heavy agent
  framework. The guarded LLM proposes only the *qualitative* plan (funnel / audiences / creatives),
  clipped to the rails; the deterministic `allocator` owns **every budget** (optimized against the
  MMM response curves — platform numbers are walled-garden, so used for reach/feasibility only,
  never cross-channel allocation). Feed the LLM only goals + rails + compact evidence (never raw
  tables); trace token cost. Same guarded-path pattern as `lens.py`; pluggable via `planner/factory.py`.
  Model access sits behind ONE gateway — `src/advanced_reporting/llm.py` (structured outputs, cost tracing, .env loading; Bedrock / Vertex / Claude Platform on AWS = one-line swap there).

## Conventions

- Python 3.12+ (the ingestion/transform modules use 3.12-only syntax), `src/` layout, package
  `advanced_reporting`. Scripts insert `src` on `sys.path`.
- Config in `config/config.yaml` (gitignored; falls back to `config.example.yaml`). Key blocks:
  `data` (source, geos, start/end window), `modeling`, `quality` (`spike_factor`, `fill_freq`),
  `reporting`. `config/engagement.yaml` (gitignored) is the per-engagement **confirmed framing**
  — provenance-stamped, written only by the dashboard Setup page (explicit `config.yaml` keys
  remain the escape hatch and outrank it). Committed structural config (no secrets/data): `config/mappings.yaml` (channel
  aliases + per-source column maps), `config/metrics.yaml` (the metric taxonomy),
  `config/campaign_goals.yaml` (goal tagging + goal→tier map), and `config/planner_rails.yaml`
  (planner hard constraints: allowed platforms, audience library, budget/cap rules, naming vocab).
- **Secrets in `.env` (gitignored). NEVER commit API keys, tokens, or data.** `.gitignore`
  excludes `.env`, `config/config.yaml`, `data/`, and `outputs/`.
- New MMM engines go behind `BaseMMM` and return an `MMMResult`; new data sources go behind
  `DataSource` and return the canonical schema — so reporting/dashboard stay agnostic.
- Tests: `pytest` in `tests/` (`pythonpath=src` set in `pyproject.toml`). Add a test when you
  add a layer or behavior. (Run `pytest -q` for the current count.)
- Keep dependencies light (pandas / numpy / scipy / matplotlib / pyyaml / streamlit / pyarrow /
  openpyxl — the last for naming-generator + planner xlsx I/O). `anthropic` is optional/lazy
  (only the guarded LLM paths import it). Meridian is the one heavy, optional dep — install separately.

## Run it

```bash
pip install -r requirements.txt
python scripts/generate_sample_data.py        # synthetic CSVs (known ground-truth DGP)
python scripts/ingest.py --source synthetic    # extract -> immutable pulls -> history.parquet (+ manifest)
python scripts/run_pipeline.py                 # store -> clean -> MMM -> outputs/ (charts, commentary, data_quality.md)
python scripts/run_pipeline.py --lens "awareness campaign"   # also writes outputs/lens_report.md
python scripts/plan_campaign.py --goal awareness --budget 100000  # goals+rails -> CampaignPlan -> names/UTMs
python scripts/advise.py --spec --commentary   # A1+A2 agents -> report_spec.json + commentary_ai.md (needs key)
python scripts/build_report.py                 # single-file HTML client report (deterministic, embeds spec + commentary)
streamlit run src/advanced_reporting/dashboard/app.py        # KPI pyramid + goal lens + free-text lens box
pytest -q
```

`plan_campaign.py` is deterministic by default; set `ANTHROPIC_API_KEY` (or `--use-llm`) for the
guarded LLM selection. Writes `outputs/campaign_plan.json` (+ budgets/trace), `campaign_plan.xlsx`,
and (unless `--no-names`) runs the generator to `outputs/trafficking_sheet.xlsx`.

## Git / two-machine setup

- **GitHub is the bridge** between Harry's work and personal machines.
- **Avoid keeping the live `.git` inside a OneDrive/Dropbox-synced folder** — the sync layer can
  lock/corrupt it. A normal local dev folder (e.g. `C:\dev\advanced-reporting`) is safest.
- The repo was seeded from `advanced-reporting.bundle`.
- **`.venv` launcher `.exe` stubs (streamlit.exe, pip.exe, etc.) hardcode the absolute path to
  `python.exe` at creation time.** If the repo folder is later moved/renamed (e.g. seeded at one
  path, then relocated into `dev projects\`), those stubs break with `Fatal error in launcher:
  Unable to create process ... The system cannot find the file specified.` — NOT a broken venv,
  NOT an IT/execution-policy issue. Fix: call the interpreter directly instead of the stub, e.g.
  `.venv\Scripts\python.exe -m streamlit run src\advanced_reporting\dashboard\app.py`. If that
  also fails, recreate the venv (`python -m venv .venv` + `pip install -r requirements.txt`).
- On a plain PowerShell window (not VS Code's integrated terminal), the venv isn't auto-activated
  — `streamlit` etc. won't be found on PATH until you activate it or use the `-m` form above. A
  PowerShell execution-policy error on `Activate.ps1` is a separate, unrelated issue (often IT
  policy on managed machines) — the `python.exe -m <tool>` form sidesteps it entirely.

## Roadmap

- **Connectors:** implement the `connectors.py` skeletons (fill in each platform's API call,
  map columns via `config/mappings.yaml`) — incl. **GA4** for engagement — or wire Supermetrics
  for one-API multi-platform pulls. Add scheduled incremental pulls so history keeps accumulating.
- **Meridian:** validate and complete the `Analyzer → MMMResult` mapping.
- **Geo-level MMM:** use `modeling_table_geo.csv` (geo×weekly) for a hierarchical/Bayesian
  model — cross-geo variation is the main lever given limited calendar history.
- **Budget optimization** against response curves is **built** (`planner/allocator.py`);
  response-curve **forecasting** on top of the model is still open.
- **Planner follow-ups:** implement the `evidence.platform_forecasts` stubs (Google
  ReachPlan/PerformancePlanner, Meta/TikTok/LinkedIn reach estimates — reach/feasibility only);
  add the demographic/audience breakdown to `ingestion/schema.py` to unlock demo-level grounding
  (`historical_performance_by_demo`); a browser-agent executor that actually books the plan.
- **Possible AWS hosting:** containerize the model step (SageMaker / Batch / ECS).

## Status

Phase 1 (thin slice), the **Phase 2 data layer**, the full **goal-aware reporting layer**, the
**campaign planner layer**, and the **intake/framing layer** (agent proposes, human confirms;
client-report gate) are complete and passing (`pytest -q`), all on synthetic data. The
pipeline runs end-to-end through the durable daily store, emits the national + geo×weekly modeling
tables and a data-quality report, fits the baseline MMM (synthetic run ≈ R² 0.85 / holdout ≈ 0.76),
and the dashboard renders the KPI pyramid + goal lens + free-text report lens. The planner turns
goals + rails into a validated `CampaignPlan` (deterministic default + guarded Sonnet-class
selection; allocator owns budgets) and round-trips into the naming generator with zero warnings. The
schema carries the mid-funnel engagement (intent) tier (populated on synthetic; `GA4Source` skeleton
ready), though engagement is **not yet aggregated** into the MMM modeling table. Real-platform
connectors + planner platform-forecasts remain fill-in-the-API skeletons; demo-level grounding awaits
a schema extension; small or collinear channels still show wide intervals — the motivation for
Meridian's Bayesian priors and geo-level modeling.
