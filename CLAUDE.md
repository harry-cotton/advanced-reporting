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

## Architecture — six layers (`src/advanced_reporting/`)

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
     `data/processed/history.parquet` (dedup grain `date,channel,campaign,geo`, keep latest;
     incremental + idempotent) and writes a manifest.
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
   `metrics.py` (tiered **KPI-pyramid** taxonomy — reach/intent/outcome — from `config/metrics.yaml`,
   campaign-goal tagging from `config/campaign_goals.yaml`, plus funnel + value-format helpers);
   `lens.py` (**free-text report lens**: NL intent → `ReportSpec` → deterministic metrics + a
   tailored narrative; deterministic keyword parser is the default, guarded LLM path optional).
5. **`dashboard/app.py`** — Streamlit dashboard: the goal-aware **KPI pyramid** + goal-lens
   selector + funnel drop-off + a free-text lens box, alongside standard non-MMM metrics.
6. **`planner/`** — turns *goals + rails* into a validated **`CampaignPlan`** that feeds the
   naming generator (Plan rows → names + UTMs). Same spine as `lens.py`: deterministic default
   + a **guarded LLM path** (one structured `claude-opus-4-8` call when `ANTHROPIC_API_KEY` is
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
- `meridian_engine.py` is wired, but its `Analyzer → MMMResult` mapping is **intentionally
  guarded (raises)** until validated against an installed Meridian version. Don't unguard it
  without actually running Meridian and checking outputs.
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
  Model access sits behind one `_llm_call` swap point (Bedrock / Vertex / direct = one-line swap).

## Conventions

- Python 3.12+ (the ingestion/transform modules use 3.12-only syntax), `src/` layout, package
  `advanced_reporting`. Scripts insert `src` on `sys.path`.
- Config in `config/config.yaml` (gitignored; falls back to `config.example.yaml`). Key blocks:
  `data` (source, geos, start/end window), `modeling`, `quality` (`spike_factor`, `fill_freq`),
  `reporting`. Committed structural config (no secrets/data): `config/mappings.yaml` (channel
  aliases + per-source column maps), `config/metrics.yaml` (the metric taxonomy),
  `config/campaign_goals.yaml` (goal tagging + goal→tier map), and `config/planner_rails.yaml`
  (planner hard constraints: allowed platforms, audience library, budget/cap rules, naming vocab).
- **Secrets in `.env` (gitignored). NEVER commit API keys, tokens, or data.** `.gitignore`
  excludes `.env`, `config/config.yaml`, `data/`, and `outputs/`.
- New MMM engines go behind `BaseMMM` and return an `MMMResult`; new data sources go behind
  `DataSource` and return the canonical schema — so reporting/dashboard stay agnostic.
- Tests: `pytest` in `tests/` (`pythonpath=src` set in `pyproject.toml`). Add a test when you
  add a layer or behavior. (Currently **85 passing**.)
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

Phase 1 (thin slice), the **Phase 2 data layer**, the full **goal-aware reporting layer**, and the
**campaign planner layer** are complete and passing (**85 tests**), all on synthetic data. The
pipeline runs end-to-end through the durable daily store, emits the national + geo×weekly modeling
tables and a data-quality report, fits the baseline MMM (synthetic run ≈ R² 0.85 / holdout ≈ 0.76),
and the dashboard renders the KPI pyramid + goal lens + free-text report lens. The planner turns
goals + rails into a validated `CampaignPlan` (deterministic default + guarded `claude-opus-4-8`
selection; allocator owns budgets) and round-trips into the naming generator with zero warnings. The
schema carries the mid-funnel engagement (intent) tier (populated on synthetic; `GA4Source` skeleton
ready), though engagement is **not yet aggregated** into the MMM modeling table. Real-platform
connectors + planner platform-forecasts remain fill-in-the-API skeletons; demo-level grounding awaits
a schema extension; small or collinear channels still show wide intervals — the motivation for
Meridian's Bayesian priors and geo-level modeling.
