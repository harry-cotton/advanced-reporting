"""End-to-end pipeline: ingest -> cleanse/organize -> MMM -> report."""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from advanced_reporting.utils import load_config
from advanced_reporting.ingestion.csv_source import CSVSource
from advanced_reporting.transform.clean import (
    load_history, clean_ad_data, to_weekly, to_weekly_geo, channel_metrics,
    build_modeling_table, build_modeling_table_geo, data_quality_report,
    data_quality_markdown)
from advanced_reporting.mmm.factory import get_engine
from advanced_reporting.reporting.charts import plot_all
from advanced_reporting.reporting.commentary import generate_commentary


def run(lens=None, sources=None, no_mmm=False, engine=None):
    cfg = load_config()
    m, rep = cfg["modeling"], cfg["reporting"]
    if engine:                       # CLI override, e.g. --engine meridian (a ~5-min MCMC)
        m = {**m, "engine": engine}
    q = cfg.get("quality", {})
    outdir = ROOT / rep["output_dir"]
    proc = ROOT / "data" / "processed"
    outdir.mkdir(parents=True, exist_ok=True)
    proc.mkdir(parents=True, exist_ok=True)

    # 1. INGEST — read the durable store (consolidated from immutable pulls), not a live
    #    fetch. Populate it first with `python scripts/ingest.py --source <name>`.
    ad_raw = load_history()

    # scope to specific extraction sources (config data.sources or --sources) so e.g.
    # the synthetic DGP and a real file-drop scenario in the same store don't blend
    sources = sources if sources is not None else cfg.get("data", {}).get("sources")
    if sources and "source" in ad_raw.columns:
        before = len(ad_raw)
        ad_raw = ad_raw[ad_raw["source"].isin(list(sources))]
        print(f"  source filter {sorted(sources)}: {before:,} -> {len(ad_raw):,} rows")
        if ad_raw.empty:
            raise SystemExit("  no rows left after the source filter — check data.sources")

    # No business-KPI series -> no MMM: descriptive mode (dashboard + non-causal
    # commentary). The KPI (e.g. CRM matchback) is what unlocks incrementality.
    # KPI path is configurable (data.kpi_path) so a self-contained dataset folder can hold
    # its own CRM matchback; defaults to data/raw/business_kpi_weekly.csv.
    kpi_rel = cfg.get("data", {}).get("kpi_path") or "data/raw/business_kpi_weekly.csv"
    kpi_path = Path(kpi_rel) if Path(kpi_rel).is_absolute() else ROOT / kpi_rel
    if not no_mmm and not kpi_path.exists():
        print(f"  {kpi_path.name} not found -> descriptive mode (no MMM)")
        no_mmm = True
    kpi = None if no_mmm else CSVSource(kpi_path, "kpi").fetch()

    # 2. CLEANSE + ORGANIZE (+ observable data-quality report)
    ad_clean, creport = clean_ad_data(ad_raw)
    weekly = to_weekly(ad_clean)                         # national (sum geos)
    weekly_geo = to_weekly_geo(ad_clean)                 # geo x weekly (long)
    channel_metrics(weekly).to_csv(proc / "channel_weekly_metrics.csv", index=False)
    weekly_geo.to_csv(proc / "modeling_table_geo.csv", index=False)

    dq = data_quality_report(ad_raw, ad_clean, creport,
                             spike_factor=q.get("spike_factor", 3.0),
                             fill_freq=q.get("fill_freq", "W-MON"))
    (outdir / "data_quality.md").write_text(data_quality_markdown(dq), encoding="utf-8")

    result = recovery = None
    charts = []
    if no_mmm:
        # descriptive mode: dashboard tables + non-causal commentary, no modeling.
        # Clear any PREVIOUS run's MMM artifacts — leaving them would let the dashboard
        # present a model fitted on a different dataset as if it were current.
        for stale in ("channel_summary.csv", "contributions.csv", "fit_metrics.json",
                      "mmm_result.json"):
            (outdir / stale).unlink(missing_ok=True)
        from advanced_reporting.reporting.commentary import generate_descriptive_commentary
        (outdir / "commentary.md").write_text(
            generate_descriptive_commentary(weekly, creport), encoding="utf-8")
    else:
        model_df = build_modeling_table(weekly, kpi, m["channel_spend_cols"],
                                        m["control_cols"], m["target"])
        model_df.to_csv(proc / "modeling_table.csv", index=False)

        # 3. MODEL (engine selected in config). Meridian is geo-level: it needs the
        #    geo x weekly table (cross-geo variation is its identifying signal); the
        #    baseline engine models the national wide table.
        if m["engine"] == "meridian":
            engine = get_engine("meridian", **(m.get("meridian") or {}))
            geo_df = build_modeling_table_geo(
                weekly_geo, kpi, m["channel_spend_cols"], m["target"], m["date_col"],
                populations=cfg.get("data", {}).get("geo_populations"))
            geo_df.to_csv(proc / "modeling_table_geo_kpi.csv", index=False)
            print(f"  meridian: geo x weekly fit — {geo_df['geo'].nunique()} geos x "
                  f"{geo_df['date'].nunique()} weeks (this runs MCMC; minutes, not seconds)")
            result = engine.fit(model_df, m["channel_spend_cols"], m["control_cols"],
                                m["target"], m["date_col"], geo_df=geo_df)
        else:
            engine = get_engine(m["engine"], train_frac=m.get("train_frac", 0.85),
                                adstock_max_lag=m.get("adstock_max_lag", 8))
            result = engine.fit(model_df, m["channel_spend_cols"], m["control_cols"],
                                m["target"], m["date_col"])
        result.channel_summary.to_csv(outdir / "channel_summary.csv", index=False)
        result.contributions.to_csv(outdir / "contributions.csv", index=False)
        (outdir / "fit_metrics.json").write_text(json.dumps(result.fit_metrics, indent=2),
                                                 encoding="utf-8")
        # full MMMResult persistence (dashboard Results page): fit + params + response
        # curves + actual-vs-predicted. Engine-agnostic — Meridian writes the same shape.
        (outdir / "mmm_result.json").write_text(json.dumps({
            "engine": result.engine, "target": m["target"],
            # count vs currency target + the client band drive the Incrementality page's
            # verdict logic (cost-per-incremental-outcome for counts, ROI-vs-1.0 for currency)
            "target_kind": m.get("target_kind", "currency"),
            "cost_per_outcome_target": m.get("cost_per_outcome_target"),
            "kpi_label": rep.get("kpi_label"),
            "fit_metrics": result.fit_metrics, "params": result.params,
            "response_curves": {ch: {"spend": list(c["spend"]),
                                     "response": list(c["response"]),
                                     "mean_spend": c["mean_spend"]}
                                for ch, c in result.response_curves.items()},
            "dates": [ts.date().isoformat() for ts in result.dates],
            "actual": list(result.actual), "predicted": list(result.predicted),
        }, indent=2, default=float), encoding="utf-8")

        # 4. REPORT
        charts = plot_all(result, outdir)
        (outdir / "commentary.md").write_text(
            generate_commentary(result, creport, m["target"],
                                target_kind=m.get("target_kind", "currency"),
                                cost_band=m.get("cost_per_outcome_target"),
                                kpi_label=rep.get("kpi_label")), encoding="utf-8")

        # 5. VALIDATE vs known ground truth (synthetic runs only; no-op on real data)
        from advanced_reporting.mmm.validation import validate_run
        recovery = validate_run(outdir)

    if lens:
        from advanced_reporting.reporting.lens import lens_report
        lens_path = outdir / "lens_report.md"
        lens_path.write_text(lens_report(weekly, lens)["narrative"], encoding="utf-8")
        print(f"  lens report -> {lens_path}")

    max_missing = max(dq["pct_missing_per_column"].values(), default=0.0)
    print("Pipeline complete.")
    print(f"  cleaned {creport['rows_in']:,} -> {creport['rows_out']:,} rows "
          f"({creport['duplicates_removed']} dupes, {creport['negatives_clipped']} negatives, "
          f"{creport['missing_values_filled']} missing fixed)")
    print(f"  data quality: {max_missing:.1f}% max col missing - "
          f"{len(dq['coverage_gaps'])} coverage-gap groups - "
          f"{len(dq['anomalies']['spend_spikes'])} spend-spike flags - "
          f"{len(dq['anomalies']['zero_spend_weeks'])} zero-spend-week flags - "
          f"currency {'MIXED' if dq['currency']['mixed'] else 'OK'}")
    if result is not None:
        print(f"  engine={result.engine}  R2={result.fit_metrics['r2']:.3f}  "
              f"holdoutR2={result.fit_metrics['test_r2']:.3f}")
    else:
        print("  descriptive mode: no MMM (add data/raw/business_kpi_weekly.csv — e.g. "
              "CRM matchback — to unlock incrementality modeling)")
    if recovery is not None:
        print(f"  ground-truth recovery: {'PASS' if recovery['passed'] else 'FAIL'} — "
              f"{recovery['n_within_tolerance']}/{recovery['n_channels']} channels within "
              f"{recovery['tolerance']:.0f}x of truth, rank corr {recovery['rank_corr']:.2f} "
              f"-> validation.md")
    print(f"  {len(charts)} charts + commentary.md + data_quality.md written to {outdir}")
    return result


if __name__ == "__main__":
    import argparse
    _ap = argparse.ArgumentParser(description="Run the reporting pipeline.")
    _ap.add_argument("--lens", default=None,
                     help="free-text report lens, e.g. 'this is an awareness campaign'")
    _ap.add_argument("--sources", default=None,
                     help="comma-separated extraction sources to include (e.g. "
                          "google_ads,meta_ads,linkedin_ads,ga4); default: config "
                          "data.sources, else all")
    _ap.add_argument("--no-mmm", action="store_true",
                     help="descriptive mode: dashboard tables + non-causal commentary, "
                          "no MMM (automatic when business_kpi_weekly.csv is absent)")
    _ap.add_argument("--engine", default=None, choices=["baseline", "meridian"],
                     help="override modeling.engine for this run (meridian runs MCMC, minutes)")
    _a = _ap.parse_args()
    run(lens=_a.lens,
        sources=[s.strip() for s in _a.sources.split(",")] if _a.sources else None,
        no_mmm=_a.no_mmm, engine=_a.engine)
