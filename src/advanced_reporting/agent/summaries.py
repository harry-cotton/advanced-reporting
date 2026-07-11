"""Compact computed summaries for agent prompts — never raw rows (the evidence.py
rule, promoted from the planner). Everything here restates artifacts the pipeline
already computed; nothing is estimated for the model's benefit.

Also owns the data hash the spec cache is keyed on: a spec generated against one
dataset is ignored (loudly) once the data changes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from ..reporting import metrics as M
from ..utils import project_root

WEEKLY_CSV = Path("data/processed/channel_weekly_metrics.csv")
HISTORY_PQ = Path("data/processed/history.parquet")
MANIFEST = Path("data/processed/history_manifest.json")
DQ_MD = Path("outputs/data_quality.md")
MMM_JSON = Path("outputs/mmm_result.json")

_DQ_CHARS = 2500      # the DQ report is already a summary; cap defensively
_MANIFEST_CHARS = 800


def data_hash(root: Path | None = None) -> str | None:
    """sha256 over the processed inputs the spec depends on; None when there is no
    processed data yet (nothing to configure against)."""
    root = root or project_root()
    weekly = root / WEEKLY_CSV
    if not weekly.exists():
        return None
    h = hashlib.sha256(weekly.read_bytes())
    hist = root / HISTORY_PQ
    if hist.exists():
        h.update(hist.read_bytes())
    return h.hexdigest()


def _round(x, nd=2):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else round(f, nd)


def _channel_rollups(weekly: pd.DataFrame) -> dict:
    cols = [c for c in ("spend", "impressions", "clicks", "conversions",
                        "key_events", "sessions", "engaged_sessions") if c in weekly]
    g = weekly.groupby("channel")[cols].sum(min_count=1)
    out: dict = {}
    for ch, row in g.iterrows():
        spend = float(row.get("spend") or 0)
        if spend <= 0:      # non-paid rows (e.g. direct) summarized separately
            continue
        d = {c: _round(row.get(c), 0) for c in cols}
        claimed, measured = row.get("conversions"), row.get("key_events")
        if measured and float(measured) > 0 and claimed is not None:
            # claim ratio = platform-claimed / analytics-measured (conversion_types.md)
            d["claim_ratio"] = _round(float(claimed) / float(measured))
        out[str(ch)] = d
    return out


def _goal_mix_by_spend(hist: pd.DataFrame) -> dict:
    """Spend share per inferred campaign goal (config/campaign_goals.yaml rules) —
    the declarative classification signal from decoded names/goal tagging."""
    if "campaign" not in hist or "spend" not in hist:
        return {}
    per = hist.groupby("campaign")["spend"].sum()
    total = float(per.sum())
    if total <= 0:
        return {}
    goals = M.load_campaign_goals()
    mix: dict[str, float] = {}
    for campaign, spend in per.items():
        goal = M.resolve_goal(str(campaign), goals)
        mix[goal] = mix.get(goal, 0.0) + float(spend)
    return {k: round(v / total, 3) for k, v in sorted(mix.items())}


def data_summary(root: Path | None = None) -> dict | None:
    """The compact summary the spec agent sees. None when the pipeline hasn't run."""
    root = root or project_root()
    weekly_f = root / WEEKLY_CSV
    if not weekly_f.exists():
        return None
    weekly = pd.read_csv(weekly_f, parse_dates=["date"])

    summary: dict = {
        "schema_columns_with_data": [c for c in weekly.columns
                                     if weekly[c].notna().any()],
        "date_range": [str(weekly["date"].min().date()),
                       str(weekly["date"].max().date())],
        "n_weeks": int(weekly["date"].nunique()),
        "paid_channel_rollups": _channel_rollups(weekly),
        "measured_outcome_exists": bool(
            "key_events" in weekly and weekly["key_events"].fillna(0).sum() > 0),
        "engagement_measured": bool(
            "sessions" in weekly and weekly["sessions"].fillna(0).sum() > 0),
        "mmm_present": (root / MMM_JSON).exists(),
    }

    hist_f = root / HISTORY_PQ
    if hist_f.exists():
        hist = pd.read_parquet(hist_f)
        summary["goal_mix_by_spend"] = _goal_mix_by_spend(hist)
        try:   # unparsed-name rate: decoded ad-level rows only; absent grain -> skip
            from ..dashboard.drilldown import unparsed_stats
            unp = unparsed_stats(hist)
            summary["unparsed_names"] = {"row_rate": round(unp["row_rate"], 3),
                                         "spend_rate": round(unp["spend_rate"], 3)}
        except Exception:
            pass

    dq_f = root / DQ_MD
    if dq_f.exists():
        summary["data_quality_report"] = dq_f.read_text(encoding="utf-8")[:_DQ_CHARS]
    man_f = root / MANIFEST
    if man_f.exists():
        summary["store_manifest"] = man_f.read_text(encoding="utf-8")[:_MANIFEST_CHARS]
    return summary


def summary_block(root: Path | None = None) -> str | None:
    s = data_summary(root)
    return None if s is None else json.dumps(s, indent=1, default=str)
