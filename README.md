# Advanced Reporting

End-to-end reporting toolkit for advertising & marketing campaigns: it **cleanses** granular
campaign data, **organizes** it into a clean modeling table, runs **Media Mix Modeling (MMM)**,
and **compiles, visualizes, and writes commentary** on the results — alongside a basic dashboard
of standard (non-MMM) metrics.

> Status: **v0.1 — thin end-to-end slice on synthetic data.** Every layer runs today on generated
> sample data with a transparent baseline MMM. Live connectors (Supermetrics et al.) and the
> Google Meridian engine are wired as the next phases.

## What it does (the five layers)

```
  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────────────┐
  │ 1. INGESTION │ → │ 2. TRANSFORM │ → │  3. MMM      │ → │ 4. REPORTING +       │
  │ CSV / API    │   │ clean +      │   │ adstock,     │   │    COMMENTARY        │
  │ (Supermetrics│   │ normalize to │   │ saturation,  │   │ charts + written     │
  │  later)      │   │ tidy weekly  │   │ contribution,│   │ analysis (guarded)   │
  │              │   │ modeling tbl │   │ ROI          │   │                      │
  └──────────────┘   └──────────────┘   └──────────────┘   └──────────────────────┘
                            │                                          
                            └────────────→ ┌────────────────────────┐
                                           │ 5. DASHBOARD           │
                                           │ ROAS / CPA / CTR /     │
                                           │ pacing (non-MMM)       │
                                           └────────────────────────┘
```

## Quickstart

```bash
# 1. Clone, then create an environment
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Configure (copies are gitignored — your secrets never get committed)
cp config/config.example.yaml config/config.yaml
cp .env.example .env

# 3. Generate ~2 years of synthetic weekly campaign data
python scripts/generate_sample_data.py

# 4. Run the full pipeline: ingest -> clean -> model -> report
python scripts/run_pipeline.py

# 5. (optional) Launch the standard-metrics dashboard
streamlit run src/advanced_reporting/dashboard/app.py
```

Outputs (charts, model metrics, written commentary) land in `outputs/`.

## Project structure

```
src/advanced_reporting/
  ingestion/   csv_source.py (works) + supermetrics.py (stub for live pulls)
  transform/   clean.py — granular daily/per-campaign data -> tidy weekly modeling table
  mmm/         base.py (interface) · transforms.py (adstock/saturation) ·
               baseline.py (works today) · meridian_engine.py (Google Meridian adapter)
  reporting/   charts.py · commentary.py (uncertainty-aware, hedges causal claims)
  dashboard/   app.py — Streamlit view of standard metrics
scripts/       generate_sample_data.py · run_pipeline.py
config/        config.example.yaml
tests/         test_pipeline.py
```

## MMM engine

The modeling layer is **pluggable** behind a common interface (`mmm/base.py`).

- **`baseline`** (default, always available): geometric adstock + Hill saturation + regularized
  regression. Fast, transparent, dependency-light — good for proving the pipeline and for
  sanity-checking the heavier engine.
- **`meridian`** (target): adapter for [Google Meridian](https://github.com/google/meridian),
  a Bayesian MMM. Heavier (TensorFlow Probability) — install separately with
  `pip install google-meridian`. Set `modeling.engine: meridian` in `config/config.yaml`.

**Interpretation caution:** MMM produces *correlational* estimates with real uncertainty. The
commentary layer is deliberately built to report ranges and hedge causal language. Treat outputs
as directional guidance to validate with experiments (geo-tests, holdouts), not as ground truth.

## Working across two machines (GitHub)

Code lives in Git; **data and credentials do not** (`.gitignore` excludes `.env`,
`config/config.yaml`, `data/`, and `outputs/`).

> **Keep the live Git repo *outside* cloud-synced folders (OneDrive/Dropbox).** A
> `.git` directory inside a synced folder fights the sync layer and can corrupt.
> GitHub itself is the bridge between your two machines — not OneDrive.

**Setting up on any machine — clone from GitHub (the only bootstrap path):**

```bash
# pick a non-synced location
mkdir C:\dev && cd C:\dev
git clone https://github.com/harry-cotton/advanced-reporting.git
cd advanced-reporting
```

Then recreate `.venv`, `pip install -r requirements.txt`, and copy your local
`.env` / `config/config.yaml` from the old machine if you have them (these never
travel through Git). Regenerate data locally — it does not sync:

```bash
python scripts/generate_sample_data.py
python scripts/ingest.py --source synthetic
```

> **Data & credentials policy:** real client data and a real `.env` must **never**
> live under a cloud-synced folder (OneDrive/Dropbox) — sync layers exfiltrate
> everything `.gitignore` protects and retain deleted copies in version history.
> Synthetic data is regenerable; real data gets its own non-synced home + backup
> (e.g. S3) when it arrives.

## Roadmap

- **Phase 2 — live data:** Supermetrics connector (Google/Meta/TikTok/LinkedIn) behind the same ingestion interface.
- **Phase 2 — Meridian:** validate the adapter against an installed Meridian version; add budget optimization & response-curve forecasting.
- **Phase 3 — agents:** scheduled refresh, automated narrative reports, and anomaly alerts.
