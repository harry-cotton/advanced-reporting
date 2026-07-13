"""Ground-truth recovery gate: grade a fitted MMM against the synthetic DGP answer key.

The project's core premise is a known data-generating process: ``generate_sample_data.py``
writes the true per-channel contribution/ROI to ``outputs/ground_truth.json``. This module
joins that answer key against the fitted ``channel_summary`` and reports how well the model
*recovered* the truth — the only accuracy measure that matters on synthetic data (fit
metrics like R² can look excellent while attribution is inverted).

Deterministic, no LLM, no network. ``validate_run(outdir)`` is the pipeline hook: it reads
``channel_summary.csv`` + ``ground_truth.json`` (+ ``fit_metrics.json`` for boundary
warnings) and writes ``validation.md``; a no-op when no answer key exists (real data).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

# Boundary values of BaselineMMM's hyperparameter searches (baseline.py). A selected value
# sitting ON a search boundary means the search wanted to go further — a red flag worth
# surfacing even when recovery looks acceptable.
DECAY_GRID_MIN, DECAY_GRID_MAX = 0.0, 0.8
RIDGE_ALPHA_GRID_MIN = 0.05

# Default pass thresholds: every channel's contribution within this factor of truth, and
# the channel *ranking* broadly right. Deliberately loose — this gate catches gross
# misattribution (20x, inversions), not calibration polish.
DEFAULT_TOLERANCE = 2.0
DEFAULT_MIN_RANK_CORR = 0.7


def load_ground_truth(path) -> dict:
    """Read ground_truth.json -> {channel: {total_spend, total_contribution, roi}}.

    Handles two answer-key schemas: the classic ``generate_sample_data`` shape
    (``{"channels": {ch: {total_spend, total_contribution, roi}}}``) and the scenario-DGP
    shape (``{"by_channel": {ch: {spend, true_incremental_submitted, roi_apps_per_1k}}}``,
    a COUNT target — ``roi`` is incremental outcomes per $, i.e. ``roi_apps_per_1k/1000``).
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "channels" in data:
        return data["channels"]
    by_ch = data.get("by_channel", {})
    return {ch: {"total_spend": v.get("spend", 0.0),
                 "total_contribution": v.get("true_incremental_submitted", 0.0),
                 "roi": v.get("roi_apps_per_1k", 0.0) / 1000.0}
            for ch, v in by_ch.items()}


def _spearman(a: pd.Series, b: pd.Series) -> float:
    if len(a) < 2:
        return float("nan")
    return float(a.rank().corr(b.rank()))


def recovery_report(channel_summary: pd.DataFrame, truth: dict,
                    fit_metrics: dict | None = None, *,
                    tolerance: float = DEFAULT_TOLERANCE,
                    min_rank_corr: float = DEFAULT_MIN_RANK_CORR) -> dict:
    """Grade the fitted summary against the answer key. Returns a plain-dict report.

    Per channel: estimated vs true contribution/ROI, the est/true ratio, and whether the
    90% contribution CI covers truth. Aggregates: Spearman rank correlation of channel
    contributions, CI coverage, worst ratio, boundary-hit warnings, and a PASS/FAIL
    verdict (every ratio within ``tolerance`` of 1 AND rank correlation >= ``min_rank_corr``).
    """
    s = channel_summary.set_index("channel")
    common = [ch for ch in truth if ch in s.index]
    missing = sorted(set(truth) - set(common))

    rows = []
    for ch in common:
        est_c = float(s.loc[ch, "contribution"])
        true_c = float(truth[ch]["total_contribution"])
        ratio = est_c / true_c if true_c else float("inf")
        covered = None
        if {"contribution_low", "contribution_high"} <= set(s.columns):
            covered = bool(s.loc[ch, "contribution_low"] <= true_c
                           <= s.loc[ch, "contribution_high"])
        rows.append({
            "channel": ch,
            "contribution_est": est_c, "contribution_true": true_c,
            "ratio_est_over_true": ratio,
            "roi_est": float(s.loc[ch, "roi"]), "roi_true": float(truth[ch]["roi"]),
            "ci_covers_truth": covered,
        })
    per = pd.DataFrame(rows)

    rank_corr = _spearman(per["contribution_est"], per["contribution_true"])
    ratios = per["ratio_est_over_true"].replace([np.inf, -np.inf], np.nan)
    worst = float(np.nanmax(np.abs(np.log(ratios.clip(lower=1e-12))))) if len(per) else float("nan")
    within = ratios.between(1.0 / tolerance, tolerance)
    coverage = per["ci_covers_truth"].mean() if per["ci_covers_truth"].notna().any() else None

    warnings = []
    if "adstock_decay" in s.columns:
        for ch in common:
            d = float(s.loc[ch, "adstock_decay"])
            if d >= DECAY_GRID_MAX - 1e-9 or d <= DECAY_GRID_MIN + 1e-9:
                warnings.append(f"{ch}: adstock decay {d:.2f} sits on the search-grid "
                                f"boundary [{DECAY_GRID_MIN}, {DECAY_GRID_MAX}]")
    alpha = (fit_metrics or {}).get("ridge_alpha")
    if alpha is not None and float(alpha) <= RIDGE_ALPHA_GRID_MIN + 1e-9:
        warnings.append(f"ridge alpha {alpha} sits at the search-grid minimum "
                        f"({RIDGE_ALPHA_GRID_MIN}) — the fit wants less regularization")
    if missing:
        warnings.append("channels in ground truth but not in the fit: " + ", ".join(missing))

    passed = bool(len(per) and within.all() and
                  (not np.isnan(rank_corr)) and rank_corr >= min_rank_corr)
    return {
        "per_channel": per.to_dict("records"),
        "rank_corr": rank_corr,
        "ci_coverage": None if coverage is None else float(coverage),
        "worst_abs_log_ratio": worst,
        "n_within_tolerance": int(within.sum()), "n_channels": len(per),
        "tolerance": tolerance, "min_rank_corr": min_rank_corr,
        "warnings": warnings,
        "passed": passed,
    }


def recovery_markdown(report: dict, *, count: bool = False) -> str:
    """Render the recovery report as markdown (written to outputs/validation.md).

    ``count`` = the target is a COUNT (submitted applications): contributions render as
    outcomes and ROI as incremental outcomes per $1k (not a currency ROI).
    """
    L = ["# MMM validation — recovery vs known ground truth\n"]
    verdict = "✅ PASS" if report["passed"] else "❌ FAIL"
    L.append(f"**{verdict}** — {report['n_within_tolerance']}/{report['n_channels']} channels "
             f"within {report['tolerance']:.1f}x of true contribution; "
             f"rank correlation {report['rank_corr']:.2f} "
             f"(threshold {report['min_rank_corr']:.1f}).\n")
    if count:
        L.append("_Count target (submitted applications): contribution is in outcomes, ROI "
                 "is incremental applications per $1,000. The strict 2x / rank-0.7 gate is "
                 "hard on the collinear pair + small channels by design; the national "
                 "baseline engine tends to FAIL it (always-on media absorbs baseline level), "
                 "while Meridian's geo-level Bayesian priors recover those channels — the "
                 "reason it is the target engine._\n")
        con = lambda v: f"{v:,.0f}"
        roi = lambda v: f"{v*1000:.2f}"
        headers = "| channel | est contribution | true contribution | est/true | est apps/$1k | true apps/$1k | CI covers truth |"
    else:
        con = lambda v: f"${v/1e6:.2f}M"
        roi = lambda v: f"{v:.2f}"
        headers = "| channel | est contribution | true contribution | est/true | est ROI | true ROI | CI covers truth |"
    L.append(headers)
    L.append("|---|---:|---:|---:|---:|---:|:---:|")
    for r in report["per_channel"]:
        cov = {True: "yes", False: "NO", None: "-"}[r["ci_covers_truth"]]
        L.append(f"| {r['channel']} | {con(r['contribution_est'])} "
                 f"| {con(r['contribution_true'])} | {r['ratio_est_over_true']:.2f} "
                 f"| {roi(r['roi_est'])} | {roi(r['roi_true'])} | {cov} |")
    if report["ci_coverage"] is not None:
        L.append(f"\n90% CI coverage of truth: {report['ci_coverage']*100:.0f}% "
                 "(high coverage with wide intervals means honest uncertainty, "
                 "not accurate point estimates).")
    if report["warnings"]:
        L.append("\n## Warnings")
        for w in report["warnings"]:
            L.append(f"- ⚠️ {w}")
    L.append("\n_This gate only exists for synthetic runs (known DGP). A FAIL means the "
             "model's attribution cannot be trusted regardless of fit metrics — see the "
             "per-channel ratios for where it goes wrong._")
    return "\n".join(L)


def validate_run(outdir) -> dict | None:
    """Pipeline hook: grade the run in ``outdir`` if an answer key exists.

    Reads channel_summary.csv + ground_truth.json (+ fit_metrics.json), writes
    validation.md, returns the report dict — or None when there is no ground truth
    (i.e. a real-data run).
    """
    outdir = Path(outdir)
    gt_path = outdir / "ground_truth.json"
    cs_path = outdir / "channel_summary.csv"
    if not gt_path.exists() or not cs_path.exists():
        return None
    raw = json.loads(gt_path.read_text(encoding="utf-8"))
    is_count = "by_channel" in raw and "channels" not in raw   # scenario-DGP count target
    truth = load_ground_truth(gt_path)
    summary = pd.read_csv(cs_path)
    fm_path = outdir / "fit_metrics.json"
    fit_metrics = json.loads(fm_path.read_text(encoding="utf-8")) if fm_path.exists() else None
    report = recovery_report(summary, truth, fit_metrics)
    (outdir / "validation.md").write_text(recovery_markdown(report, count=is_count),
                                          encoding="utf-8")
    return report
