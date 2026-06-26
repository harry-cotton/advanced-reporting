"""Visualize MMM results. Saves PNGs to the output directory; returns their paths."""
from __future__ import annotations
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _save(fig, path):
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def plot_all(result, outdir) -> dict:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    paths = {}

    # 1. model fit
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(result.dates, result.actual, label="Actual", lw=2)
    ax.plot(result.dates, result.predicted, label="Model", lw=2, ls="--")
    ax.set_title(f"Model fit — {result.engine} (R²={result.fit_metrics['r2']:.2f}, "
                 f"holdout R²={result.fit_metrics['test_r2']:.2f})")
    ax.set_ylabel("Revenue"); ax.legend()
    paths["fit"] = _save(fig, outdir / "fit_actual_vs_predicted.png")

    # 2. decomposition over time
    c = result.contributions.copy()
    dcol = c.columns[0]
    order = ["baseline"] + [x for x in c.columns if x not in (dcol, "baseline")]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.stackplot(c[dcol], *[c[k].clip(lower=0) for k in order], labels=order)
    ax.set_title("Revenue decomposition over time"); ax.set_ylabel("Revenue")
    ax.legend(loc="upper left", ncol=3, fontsize=8)
    paths["decomp"] = _save(fig, outdir / "contribution_over_time.png")

    s = result.channel_summary
    # 3. contribution with CI
    fig, ax = plt.subplots(figsize=(8, 4))
    yerr = np.vstack([np.clip(s.contribution - s.contribution_low, 0, None),
                      np.clip(s.contribution_high - s.contribution, 0, None)])
    ax.bar(s.channel, s.contribution, yerr=yerr, capsize=4)
    ax.set_title("Estimated channel contribution (90% interval)"); ax.set_ylabel("Revenue")
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    paths["contrib"] = _save(fig, outdir / "channel_contribution.png")

    # 4. ROI with CI
    fig, ax = plt.subplots(figsize=(8, 4))
    yerr = np.vstack([np.clip(s.roi - s.roi_low, 0, None), np.clip(s.roi_high - s.roi, 0, None)])
    ax.bar(s.channel, s.roi, yerr=yerr, capsize=4, color="seagreen")
    ax.axhline(1.0, color="red", ls="--", lw=1, label="ROI = 1")
    ax.set_title("Estimated ROI by channel (90% interval)"); ax.set_ylabel("Revenue per $ spend")
    ax.legend(); plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    paths["roi"] = _save(fig, outdir / "channel_roi.png")

    # 5. saturation / response curves
    rc = result.response_curves
    n = len(rc); cols = min(3, n); rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(4 * cols, 3 * rows), squeeze=False)
    for i, (ch, cv) in enumerate(rc.items()):
        ax = axes[i // cols][i % cols]
        ax.plot(cv["spend"], cv["response"])
        ax.axvline(cv["mean_spend"], color="orange", ls="--", lw=1, label="avg spend")
        ax.set_title(ch, fontsize=9); ax.legend(fontsize=7)
    for j in range(n, rows * cols):
        axes[j // cols][j % cols].axis("off")
    fig.suptitle("Saturation / response curves"); fig.tight_layout()
    paths["curves"] = _save(fig, outdir / "response_curves.png")
    return paths
