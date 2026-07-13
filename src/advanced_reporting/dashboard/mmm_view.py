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

import numpy as np
import pandas as pd

# Default client band for a COUNT target ($ per incremental outcome) when the run meta
# carries none — mirrors config modeling.cost_per_outcome_target.
_DEFAULT_COST_BAND = {"good": 400.0, "warn": 650.0}


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


def is_count_target(meta: dict) -> bool:
    """True when the MMM target is a COUNT (e.g. submitted applications), not currency.

    For a count target the ROI number is incremental-outcomes-per-$ (≈0.005) and grading
    it against 1.0 is meaningless — the page grades cost PER incremental outcome instead.
    """
    return str(meta.get("target_kind", "currency")).lower() == "count"


def cost_per_outcome_intervals(summary: pd.DataFrame, meta: dict) -> pd.DataFrame:
    """Per-channel COST per incremental outcome ($ / incremental application) + 90% interval
    + a client-band verdict, cheapest first.

    Cost = spend / incremental contribution, so the interval FLIPS the contribution CI
    (best case = spend/contribution_high). Graded against ``cost_per_outcome_target``:
    interval entirely below ``good`` = strong; entirely above ``warn`` = cut candidate;
    spanning (or an interval that reaches 'infinite' because the model can't rule out zero
    incremental effect) = unproven. Provenance label: "client target".
    """
    band = meta.get("cost_per_outcome_target") or _DEFAULT_COST_BAND
    good, warn = float(band.get("good")), float(band.get("warn"))
    out = summary[["channel", "spend", "contribution",
                   "contribution_low", "contribution_high"]].copy()

    def _cost(spend, contrib):
        # sub-1-application contribution over the whole flight = no measurable effect
        return float(spend) / contrib if contrib and contrib > 1.0 else np.inf

    out["cost_per"] = [_cost(s, c) for s, c in zip(out["spend"], out["contribution"])]
    out["cost_low"] = [_cost(s, c) for s, c in zip(out["spend"], out["contribution_high"])]
    out["cost_high"] = [_cost(s, c) for s, c in zip(out["spend"], out["contribution_low"])]

    def _verdict(r):
        if not np.isfinite(r["cost_high"]):
            return "unproven"                     # can't rule out zero incremental effect
        if r["cost_high"] <= good:
            return "strong"                       # even the worst case beats the good band
        if r["cost_low"] > warn:
            return "cut_candidate"                # confidently measurable AND above warn
        return "unproven"                         # spans the band

    out["verdict"] = [_verdict(r) for _, r in out.iterrows()]
    out["good"], out["warn"] = good, warn
    return out.sort_values("cost_per").reset_index(drop=True)


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
