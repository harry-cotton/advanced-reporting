"""Side-by-side engine validation: TRUTH vs BASELINE vs MERIDIAN (FBI recruiting P4).

Fits both MMM engines on the SAME data — the national wide table (baseline) and the geo x
weekly table (Meridian) built from the durable store — and grades each against the known
ground truth (``outputs/ground_truth.json``). Writes ``outputs/engine_comparison.md``:
per-channel ROI + 90% intervals, ROI rank order, contribution shares, interval coverage,
and how the designed stress cases behave under each engine.

Meridian runs a real MCMC (minutes on CPU) — manual/local only. If google-meridian is not
installed the report is written baseline-only, saying so loudly (never silently skipped).

    python scripts/compare_engines.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from advanced_reporting.utils import load_config
from advanced_reporting.transform.clean import (
    load_history, clean_ad_data, to_weekly, to_weekly_geo, build_modeling_table,
    build_modeling_table_geo)
from advanced_reporting.ingestion.csv_source import CSVSource
from advanced_reporting.mmm.factory import get_engine
from advanced_reporting.mmm.validation import load_ground_truth, recovery_report


def _spearman(est, true):
    a, b = pd.Series(est), pd.Series(true)
    return float(a.rank().corr(b.rank())) if len(a) > 1 else float("nan")


def main() -> int:
    cfg = load_config()
    m = cfg["modeling"]
    chans, target = m["channel_spend_cols"], m["target"]
    outdir = ROOT / cfg["reporting"]["output_dir"]

    gt_path = outdir / "ground_truth.json"
    if not gt_path.exists():
        print(f"No {gt_path} — run generate_fbi_campaign.py first."); return 1
    truth = load_ground_truth(gt_path)
    truth_full = json.loads(gt_path.read_text(encoding="utf-8"))

    hist, _ = clean_ad_data(load_history())
    weekly = to_weekly(hist)
    weekly_geo = to_weekly_geo(hist)
    kpi = CSVSource(ROOT / (cfg["data"].get("kpi_path") or "data/raw/business_kpi_weekly.csv"),
                    "kpi").fetch()
    nat = build_modeling_table(weekly, kpi, chans, m["control_cols"], target)

    # --- baseline (national) ---
    print("Fitting baseline (national)...")
    t0 = time.time()
    base = get_engine("baseline", train_frac=m.get("train_frac", 0.85)).fit(
        nat, chans, m["control_cols"], target, m["date_col"])
    print(f"  baseline {time.time()-t0:.0f}s")
    reports = {"baseline": (base, recovery_report(base.channel_summary, truth))}

    # --- meridian (geo) ---
    try:
        geo_df = build_modeling_table_geo(weekly_geo, kpi, chans, target, m["date_col"],
                                          populations=cfg["data"].get("geo_populations"))
        print(f"Fitting Meridian (geo: {geo_df['geo'].nunique()} geos x "
              f"{geo_df['date'].nunique()} weeks; MCMC, minutes)...")
        t0 = time.time()
        mer = get_engine("meridian", **(m.get("meridian") or {})).fit(
            nat, chans, m["control_cols"], target, m["date_col"], geo_df=geo_df)
        print(f"  meridian {time.time()-t0:.0f}s")
        reports["meridian"] = (mer, recovery_report(mer.channel_summary, truth))
    except Exception as e:
        print(f"  MERIDIAN UNAVAILABLE ({type(e).__name__}: {e}) — baseline-only report.")

    (outdir / "engine_comparison.md").write_text(
        _markdown(truth, truth_full, reports), encoding="utf-8")
    print(f"Wrote {outdir/'engine_comparison.md'}")
    for name, (res, rep) in reports.items():
        base_share = _baseline_share(res)
        print(f"  {name:<9} rank {rep['rank_corr']:.2f}  within2x {rep['n_within_tolerance']}"
              f"/{rep['n_channels']}  CIcov {rep['ci_coverage'] or 0:.2f}  "
              f"held-out R² {res.fit_metrics['test_r2']:.2f}  baseline≈{base_share:.0%} of KPI")
    return 0


def _baseline_share(res) -> float:
    if res.contributions is not None and "baseline" in res.contributions:
        b = float(res.contributions["baseline"].sum())
        tot = b + float(res.channel_summary["contribution"].sum())
        return b / tot if tot else float("nan")
    return float("nan")


def _markdown(truth, truth_full, reports) -> str:
    engines = list(reports)
    L = ["# MMM engine validation — truth vs " + " vs ".join(engines) + "\n"]
    ident = truth_full.get("identity", {})
    L.append(f"Known DGP: paid drives **{ident.get('paid_share', 0)*100:.0f}%** of submitted "
             f"applications (baseline {ident.get('baseline', 0):,.0f} + paid "
             f"{ident.get('paid_contribution', 0):,.0f} = {ident.get('kpi_submitted', 0):,.0f}). "
             "ROI = incremental submitted applications per $1,000.\n")

    L.append("## Headline\n")
    L.append("| engine | ROI rank corr | within 2x | 90% CI coverage | held-out R² | baseline share |")
    L.append("|---|---:|---:|---:|---:|---:|")
    true_share = ident.get("paid_share")
    for name, (res, rep) in reports.items():
        bs = _baseline_share(res)
        L.append(f"| {name} | {rep['rank_corr']:.2f} | {rep['n_within_tolerance']}"
                 f"/{rep['n_channels']} | {(rep['ci_coverage'] or 0)*100:.0f}% | "
                 f"{res.fit_metrics['test_r2']:.2f} | {(1-bs)*100:.0f}% paid |")
    if true_share is not None:
        L.append(f"\n_Truth: paid share {true_share*100:.0f}%. An engine over-crediting paid "
                 "reads a higher paid share than truth._\n")

    # per-channel ROI
    L.append("## Per-channel ROI (apps per $1,000) — point + 90% interval\n")
    header = "| channel | true | " + " | ".join(f"{e} (90% CI)" for e in engines) + " | posture |"
    L.append(header); L.append("|---" * (2 + len(engines)) + "|---|")
    order = sorted(truth, key=lambda c: -truth[c]["roi"])
    for ch in order:
        tr = truth[ch]["roi"] * 1000
        cells = [f"{ch}", f"{tr:.2f}"]
        for name in engines:
            s = reports[name][0].channel_summary.set_index("channel")
            if ch in s.index:
                r = s.loc[ch]
                cover = "✓" if r["roi_low"]*1000 <= tr <= r["roi_high"]*1000 else "✗"
                cells.append(f"{r['roi']*1000:.2f} [{r['roi_low']*1000:.2f}–"
                             f"{r['roi_high']*1000:.2f}] {cover}")
            else:
                cells.append("—")
        posture = truth_full.get("by_channel", {}).get(ch, {}).get("roi_posture", "")
        cells.append(posture)
        L.append("| " + " | ".join(cells) + " |")

    # stress-case read
    L.append("\n## Stress cases — do the engines behave as designed?\n")
    L.append(_stress_notes(truth, reports))
    L.append("\n_Both engines are correlational estimates. The point of the comparison is "
             "whether Meridian's Bayesian priors + geo hierarchy tighten the intervals and "
             "the paid/baseline split relative to the national baseline engine — the reason "
             "Meridian is the target engine._")
    return "\n".join(L)


def _stress_notes(truth, reports) -> str:
    notes = []

    def _ci(name, ch):
        s = reports[name][0].channel_summary.set_index("channel")
        if ch not in s.index:
            return None
        r = s.loc[ch]
        return r["roi_low"] * 1000, r["roi"] * 1000, r["roi_high"] * 1000

    for name in reports:
        yt, mt = _ci(name, "youtube"), _ci(name, "meta")
        au = _ci(name, "audio")
        parts = []
        if yt and mt:
            parts.append(f"collinear pair meta/youtube — youtube CI width "
                         f"{yt[2]-yt[0]:.1f}, meta {mt[2]-mt[0]:.1f} (wide = the pair is "
                         "hard to separate, as designed)")
        if au:
            spans = au[0] <= 0.9 <= au[2] or au[0] < 0.5
            parts.append(f"audio {'spans break-even (unproven ✓)' if spans else 'CI '+f'[{au[0]:.2f}-{au[2]:.2f}]'}")
        notes.append(f"- **{name}**: " + "; ".join(parts) + ".")
    return "\n".join(notes)


if __name__ == "__main__":
    raise SystemExit(main())
