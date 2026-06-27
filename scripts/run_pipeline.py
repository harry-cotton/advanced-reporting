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
    build_modeling_table, data_quality_report, data_quality_markdown)
from advanced_reporting.mmm.factory import get_engine
from advanced_reporting.reporting.charts import plot_all
from advanced_reporting.reporting.commentary import generate_commentary


def run(lens=None):
    cfg = load_config()
    m, rep = cfg["modeling"], cfg["reporting"]
    q = cfg.get("quality", {})
    outdir = ROOT / rep["output_dir"]
    proc = ROOT / "data" / "processed"
    outdir.mkdir(parents=True, exist_ok=True)
    proc.mkdir(parents=True, exist_ok=True)

    # 1. INGEST — read the durable store (consolidated from immutable pulls), not a live
    #    fetch. Populate it first with `python scripts/ingest.py --source <name>`.
    ad_raw = load_history()
    kpi = CSVSource(ROOT / "data/raw/business_kpi_weekly.csv", "kpi").fetch()

    # 2. CLEANSE + ORGANIZE (+ observable data-quality report)
    ad_clean, creport = clean_ad_data(ad_raw)
    weekly = to_weekly(ad_clean)                         # national (sum geos)
    weekly_geo = to_weekly_geo(ad_clean)                 # geo x weekly (long)
    channel_metrics(weekly).to_csv(proc / "channel_weekly_metrics.csv", index=False)
    weekly_geo.to_csv(proc / "modeling_table_geo.csv", index=False)
    model_df = build_modeling_table(weekly, kpi, m["channel_spend_cols"],
                                    m["control_cols"], m["target"])
    model_df.to_csv(proc / "modeling_table.csv", index=False)

    dq = data_quality_report(ad_raw, ad_clean, creport,
                             spike_factor=q.get("spike_factor", 3.0),
                             fill_freq=q.get("fill_freq", "W-MON"))
    (outdir / "data_quality.md").write_text(data_quality_markdown(dq), encoding="utf-8")

    # 3. MODEL (engine selected in config)
    engine = get_engine(m["engine"], train_frac=m.get("train_frac", 0.85),
                        adstock_max_lag=m.get("adstock_max_lag", 8))
    result = engine.fit(model_df, m["channel_spend_cols"], m["control_cols"],
                        m["target"], m["date_col"])
    result.channel_summary.to_csv(outdir / "channel_summary.csv", index=False)
    result.contributions.to_csv(outdir / "contributions.csv", index=False)
    (outdir / "fit_metrics.json").write_text(json.dumps(result.fit_metrics, indent=2),
                                             encoding="utf-8")

    # 4. REPORT
    charts = plot_all(result, outdir)
    (outdir / "commentary.md").write_text(
        generate_commentary(result, creport, m["target"]), encoding="utf-8")

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
    print(f"  engine={result.engine}  R2={result.fit_metrics['r2']:.3f}  "
          f"holdoutR2={result.fit_metrics['test_r2']:.3f}")
    print(f"  {len(charts)} charts + commentary.md + data_quality.md written to {outdir}")
    return result


if __name__ == "__main__":
    import argparse
    _ap = argparse.ArgumentParser(description="Run the reporting pipeline.")
    _ap.add_argument("--lens", default=None,
                     help="free-text report lens, e.g. 'this is an awareness campaign'")
    run(lens=_ap.parse_args().lens)
