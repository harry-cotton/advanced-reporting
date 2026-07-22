> **Status: proposed design — NOT yet implemented.** Captured 2026-07-22 from a planning
> session after real platform exports (Meta / Google / Snapchat) exposed a data-loss bug and
> rigid readers in the file-drop ingestion path. Kept for when there's budget to build it.

# Plan: Robust ingestion for messy real-world ad-platform exports

## Context

The tool's premise is *cleansing messy real-world campaign data*, but the file-drop ingestion
(`scripts/ingest.py --inbox` → `ingestion/exports.py` → `ingestion/store.py` → `transform/clean.py`)
only accepts pristine, correctly-shaped exports. Testing three real exports (Meta, Google, Snapchat)
exposed two failures:

1. **Silent data loss on unmodeled breakdowns.** Exports broken down by Age/Gender/Device produce
   many rows sharing one grain key. `store.consolidate` collapses them **keep-last** (dropping, not
   summing) — a real Meta file lost **16,719 of 17,215 rows** and reported ~3% of true spend.
2. **Rigid readers.** Detection keys on exact title rows (`Campaign report`) and readers read exact
   column names (`df["Amount spent"]`, `df["Campaign ID"]`, `df["Conv. value"]`). A different report
   type, a missing optional column, or an unknown platform (Snapchat) is rejected outright.

This matters most in the real use case: inheriting years of data from an agency who won't re-pull.
Asking for a re-export is the tool failing. **Goal:** the cleaning layer aggregates away dimensions it
doesn't model, resolves varied column names, tolerates missing optional columns, and ingests unknown
platforms via config — not code. Outcome: all three sample files load and produce a level-1 dashboard
untouched.

Two phases: **Phase 1** = the correctness core (fixes the data loss + column tolerance). **Phase 2** =
config-driven generic reader + tolerant detection (unknown platforms via `mappings.yaml`).

## Key design decision: aggregate at consolidate-time, not at write-time

The durable raw store keeps every breakdown row on disk verbatim (`write_pull` is unchanged). Grain
aggregation happens when `consolidate()` builds `history.parquet` — so re-consolidating after a grain
change re-derives from full detail, and nothing is lost on disk. Within one pull we **SUM** to grain;
across pulls we keep the existing **keep-last** restatement semantics. These become physically
separate operations.

---

## Phase 1 — Correctness core

### 1.1 Shared summable-column constants — `ingestion/schema.py`
Alongside `METRIC_COLUMNS` (line 123) add:
```python
ENGAGEMENT_COLUMNS = ("sessions", "engaged_sessions", "key_events", "page_views", "video_views")
SUMMABLE_COLUMNS   = METRIC_COLUMNS + ENGAGEMENT_COLUMNS   # avg_engagement_seconds excluded (it's an average)
```
Point `transform/clean.py` (`METRIC_COLS`/`ENGAGEMENT_COLS`, lines 16/21) at these so the store, cleaner
and weekly-agg share one source of truth. Pure refactor — suite must stay green.

### 1.2 Per-pull grain aggregation — `ingestion/store.py`
New helper `_collapse_to_grain(df)`, called in the `consolidate` loop **after** label/text normalization
and **before** `frames.append(df)` (between lines 209–210):
- `groupby(list(KEY_COLS), as_index=False, dropna=False)` then aggregate:
  - `SUMMABLE_COLUMNS` present → **sum with `min_count=1`** (CRITICAL: keeps an all-NaN group as NaN =
    "not measured", never 0.0 — otherwise GA4/coexistence tests break and the schema's NaN contract is violated).
  - other canonical non-key cols (`currency`, `audience_*`, `creative*`, `avg_engagement_seconds`) → `first()`
    (constant within a KEY_COLS group; `first()` for `avg_engagement_seconds` is a documented approximation).
  - non-canonical breakdown cols (Age/Gender/Device/…) → dropped by not selecting them.
- End with `schema.normalize(out)` to restore canonical column order.
- Keep `dropna(subset=["date"])` at line 219 as the single place undated rows are removed.

Because every appended frame now has unique `KEY_COLS`, the unchanged
`drop_duplicates(subset=KEY_COLS, keep="last")` (line 220) only ever chooses between *pulls* (restatements).
The `supersede` step (campaign vs ad-level, lines 226–239) is unaffected.

Reword the `dup_keys` warning (lines 205–209) from "collapsed keep-last" to informational: *"N same-key
row(s) summed to grain (unmodeled breakdown aggregated away); map campaign_id/account_id if these are
actually distinct campaigns."* Keep it a `warnings.warn` and keep writing `dup_key_rows` to the manifest.

### 1.3 Column-synonym resolution + missing-optional tolerance
- Add a **`column_synonyms`** section to `config/mappings.yaml` (canonical field → accepted raw header
  variants), e.g. `spend: [spend, cost, "amount spent", "total spent", "cost_usd"]`,
  `impressions: [impressions, "impr.", impr]`, `conversions: [conversions, results, "conv."]`,
  `platform_revenue: ["conv. value", "conversion value", action_values]`, `date: [date, day, date_start]`,
  `geo: [geo, region, country, market]`, `channel: [channel, "campaign type", platform]`.
- Add `resolve_synonyms(df, mappings)` next to `apply_source_map` in `schema.py` (same non-destructive
  contract: normalize headers lower/strip/collapse-punct, rename the first present variant to canonical,
  skip any canonical column already present so exact per-source maps win).
- Add `_pick(df, *variants, required=True, default=None)` helper in `exports.py`: first present column
  among variants (case/space-insensitive) as a Series, else `default` (optional) or raise `SchemaError`
  (required). Retrofit the bespoke readers' metric/optional lookups to use it — so a Google export missing
  `Conv. value` or `Region` no longer throws (`platform_revenue`→NaN, `geo`→`national`). Keep each reader's
  platform-specific quirk handling (currency-from-header, LinkedIn preamble account id, locale dates).

---

## Phase 2 — Config-driven generic reader + tolerant detection

### 2.1 Loosen `detect_format` (`exports.py:76-97`)
Keep all bespoke sniffs first (no regression). Replace the strict generic check (lines 94–96) with a
**synonym-based signature**: run the header through `resolve_synonyms`; if it covers `date` + `spend` +
one of {`impressions`,`clicks`} + `campaign`, return `GENERIC_CAMPAIGN`. `channel`/`geo`/`currency` no
longer required at detection time. Files matching nothing still return `None` → still refused loudly.

### 2.2 Generalize `read_generic_campaign_export` (`exports.py:303-323`)
- `resolve_synonyms(pd.read_csv(path), mappings)` before `apply_source_map(df, "campaign_delivery", ...)`.
- Reuse existing `_add_region` (geo) and `_num` (numeric parse).
- **Channel resolution** when no `channel` column — add a `generic_export` section to `mappings.yaml`:
  `filename_channel_hints` (substring in filename → canonical channel, e.g. `snapchat: snapchat`) and
  `default_channel: null`. Order: `channel` column → per-file override → filename hint → default →
  **raise `SchemaError`** (never guess). Add `channel_aliases` entries for new channels (`snapchat`, etc.).
- Currency discipline unchanged: read from file, else `to_canonical` refuses to assume USD (unless override).

### 2.3 Optional per-file overrides
`read_export(path, mappings=None, *, channel=None, currency=None)` threaded to the generic reader; expose
optional `--channel`/`--currency` flags on `scripts/ingest.py --inbox`. Config-hint path stays the default.
**Adding Snapchat becomes: drop CSV + add synonyms/filename-hint/channel-alias in `mappings.yaml`. No code.**

---

## Files to modify
- `src/advanced_reporting/ingestion/schema.py` — constants (1.1), `resolve_synonyms` (1.3).
- `src/advanced_reporting/ingestion/store.py` — `_collapse_to_grain` + wire-in, warning reword (1.2).
- `src/advanced_reporting/ingestion/exports.py` — `_pick`, retrofit readers (1.3), `detect_format` +
  generic reader + overrides (2.1–2.3).
- `src/advanced_reporting/transform/clean.py` — import shared constants (1.1).
- `config/mappings.yaml` — `column_synonyms`, `generic_export`, new `channel_aliases` (1.3, 2.2).
- Tests + docs (below).

## Test impact & new tests
- **Update** `tests/test_store_hardening.py::test_within_pull_key_collision_warns` — flips from documenting
  loss to proving SUM: adjust `match=` to the new wording and add `assert hist["spend"].iloc[0] == 350.0`.
- **Must stay green** (guardrails for the NaN/grain edges): `test_same_named_campaigns_in_different_accounts_both_survive`,
  `test_ga4_rows_coexist_with_ad_rows_on_same_grain`, `test_web_analytics_source_fills_ad_metrics_and_claims_no_currency`,
  `test_inbox_to_store_to_weekly_end_to_end`, `test_mixed_grain_ingest_does_not_double_count`, `test_detect_format`.
- **New** (small inline fixtures, mirroring existing `_adobe_csv`/`ga4_bad` patterns):
  demographic-breakdown Meta file (grain collapses by SUM; extras absent from history) — the direct guard
  against the 16,719-row loss; NaN-preservation (all-NaN engagement stays NaN); alt-column-name file
  (`Spend`/`Conversion value` resolve); missing-optional file (no `Conv. value`/`Region` → ingests, NaN/national);
  Snapchat-style unknown-platform file (detects generic, channel from filename hint) + a negative test that an
  unresolved-channel generic file raises `SchemaError`.

## Sequencing (each step independently green & shippable)
1. Constants only (1.1) — pure refactor, full suite green.
2. Per-pull aggregation (1.2) + update/add store tests.
3. Reader tolerance: `column_synonyms` + `resolve_synonyms` + `_pick` (1.3) + alt-column/missing-optional tests.
4. Tolerant detection + generic reader + overrides (2.1–2.3) + Snapchat tests.
5. Docs: `data/inbox/README.md` + `mappings.yaml` header comments (synonyms, generic ingest, adding a platform).

## Verification
- **Unit:** after each step `pytest tests/test_store_hardening.py tests/test_exports.py tests/test_pipeline.py tests/test_fbi_ingest.py -q`; full `pytest -q` before finishing.
- **End-to-end** on a messy fixture folder (breakdown + Snapchat files): `python scripts/ingest.py --inbox "<folder>"`
  → manifest shows `dup_key_rows > 0`, Snapchat under a `snapchat` channel, spend reconciles to the breakdown row-sum.
- **Level-1 pipeline:** `python scripts/run_pipeline.py --no-mmm` → no mixed-currency raise, weekly spend == history spend, data-quality report produced.
- **Dashboard:** launch Streamlit; Data Quality + Channels pages render and breakdown-heavy platforms show full (summed) spend, not truncated totals.
- Note (Windows): call the interpreter directly — `.venv\Scripts\python.exe scripts\...` / `.venv\Scripts\python.exe -m streamlit run ...` (per CLAUDE.md launcher-stub note).

## Not in scope
Adding Age/Gender/Device as modeled schema dimensions (demographic-level MMM) — this plan sums them away.
Real API connectors remain skeletons. No changes to the MMM engines or reporting.
