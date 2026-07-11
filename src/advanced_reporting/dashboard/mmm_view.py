"""Pure loaders/shapers for the MMM Results page (redesign U3) — no Streamlit.

Reads the pipeline's model artifacts (``outputs/channel_summary.csv`` +
``outputs/mmm_result.json`` + optional ``contributions.csv``) and reshapes them for the
page. Engine-agnostic by construction: everything works off the persisted ``MMMResult``
shape, so the baseline engine today and Meridian later render identically (Meridian
simply brings tighter intervals).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def load_mmm(outdir: Path) -> dict | None:
    """Load the latest model run, or None when no (complete) run exists.

    A descriptive (no-MMM) pipeline run deletes these artifacts, so their presence
    means "this model belongs to the data currently in outputs/".
    """
    outdir = Path(outdir)
    summary_f = outdir / "channel_summary.csv"
    meta_f = outdir / "mmm_result.json"
    if not summary_f.exists() or not meta_f.exists():
        return None
    out = {"summary": pd.read_csv(summary_f),
           "meta": json.loads(meta_f.read_text(encoding="utf-8"))}
    contrib_f = outdir / "contributions.csv"
    out["contributions"] = pd.read_csv(contrib_f) if contrib_f.exists() else None
    return out


def waterfall_items(summary: pd.DataFrame,
                    contributions: pd.DataFrame | None) -> list[tuple[str, float]]:
    """Ordered (label, value) pairs for the contribution waterfall.

    Baseline first (from the weekly contributions when available), then channels by
    estimated contribution, descending. Values are point estimates — the page shows
    the intervals alongside, never pretending these are exact.
    """
    items: list[tuple[str, float]] = []
    if contributions is not None and "baseline" in contributions.columns:
        items.append(("Baseline", float(contributions["baseline"].sum())))
    s = summary.sort_values("contribution", ascending=False)
    items += [(str(r["channel"]), float(r["contribution"])) for _, r in s.iterrows()]
    return items


def roi_intervals(summary: pd.DataFrame) -> pd.DataFrame:
    """Channels with ROI point + 90% interval, sorted by point ROI descending."""
    cols = ["channel", "roi", "roi_low", "roi_high"]
    out = summary[cols].copy().sort_values("roi", ascending=False).reset_index(drop=True)
    # honesty flags the page colors by: whole interval above 1 = confidently profitable,
    # whole interval below 1 = confidently unprofitable, else unproven
    out["verdict"] = "unproven"
    out.loc[out["roi_low"] >= 1.0, "verdict"] = "profitable"
    out.loc[out["roi_high"] < 1.0, "verdict"] = "unprofitable"
    return out


def response_curves(meta: dict) -> dict[str, dict]:
    """Per-channel response curve {spend[], response[], mean_spend} from the run meta."""
    return dict(meta.get("response_curves") or {})


def fit_cards(meta: dict) -> list[tuple[str, str, str | None]]:
    """(label, value, help) cards for the fit strip — held-out figures lead."""
    fm = meta.get("fit_metrics") or {}

    def _pct(x):
        return f"{x * 100:.1f}%" if x is not None else "—"

    def _r2(x):
        return f"{x:.2f}" if x is not None else "—"

    return [
        ("Engine", str(meta.get("engine", "?")),
         "baseline = adstock+saturation ridge regression; meridian = Bayesian (target)"),
        ("Held-out R²", _r2(fm.get("test_r2")),
         "Accuracy on weeks the model never saw — the reliability guide."),
        ("Held-out MAPE", _pct(fm.get("test_mape")),
         "Average % error on held-out weeks; lower is better."),
        ("In-sample R²", _r2(fm.get("r2")),
         "Fit on training weeks — always flattering; trust the held-out figure more."),
    ]
