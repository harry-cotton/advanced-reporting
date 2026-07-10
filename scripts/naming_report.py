"""Rank still-unparsed ad-set / creative names by spend and draft crosswalk suggestions.

Workflow to remediate inconsistent client naming:
  1. python scripts/ingest.py --inbox           # load the raw exports
  2. python scripts/naming_report.py            # this: what's unparsed + draft mappings
  3. review config/naming_overrides.suggested.yaml, move good entries into
     config/naming_overrides.yaml (edit the fields — suggestions are drafts, never trusted)
  4. python scripts/ingest.py --inbox --reset   # re-decode with the crosswalk applied

Suggestions are heuristic DRAFTS for human review — they are NOT applied automatically.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from advanced_reporting.ingestion import naming_decode          # noqa: E402
from advanced_reporting.utils import load_naming_overrides       # noqa: E402

_NOISE = re.compile(r"\b(feed|reels?|search|discover|broad|phrase|exact|test|old|legacy|"
                    r"copy|final|v\d+)\b")


def suggest(name: str, channel: str) -> dict:
    """Draft a mapping for one unparsed name. Rough on purpose — a human edits it."""
    n = name.lower()
    if channel == "linkedin":                                    # LinkedIn = creative grain
        fmt = ("VID" if re.search(r"vid|video", n) else "CAROUSEL" if "carousel" in n
               else "STATIC" if re.search(r"static|image|img|one[- ]?pager|doc", n) else "")
        head = re.split(r"video|static|carousel|-|_|\(|\s", n)[0]
        creative = re.sub(r"[^a-z0-9]", "", head).upper()[:16] or "CREATIVE"
        return {"creative": creative, "creative_format": fmt}
    atype = "RETARGET" if re.search(r"\brt\b|retarget|remarket|site|visitor", n) else "PROSPECT"
    detail = re.sub(r"[^a-z0-9]+", "-", _NOISE.sub(" ", n)).strip("-").upper()[:20] or "UNSET"
    return {"audience_type": atype, "audience_detail": detail}


def main() -> None:
    hist_path = ROOT / "data" / "processed" / "history.parquet"
    if not hist_path.exists():
        print("No history.parquet — run `python scripts/ingest.py --inbox` first.")
        return
    hist = pd.read_parquet(hist_path)
    ov = load_naming_overrides()
    ad = hist[hist["ad_group"].fillna("").astype(str).str.strip() != ""].copy()
    if ad.empty:
        print("No ad-level rows in the store.")
        return
    decoded = naming_decode.decode_series(ad["ad_group"], overrides=ov)
    ad["_at"] = decoded["audience_type"].to_numpy()
    unp = ad[ad["_at"] == naming_decode.UNPARSED]

    total_spend = float(ad["spend"].sum())
    unp_spend = float(unp["spend"].sum())
    print(f"Ad-level spend ${total_spend:,.0f} · unparsed ${unp_spend:,.0f} "
          f"({unp_spend / total_spend:.0%}) across {ad['ad_group'].nunique()} names "
          f"({len(ov)} already mapped in the crosswalk).\n")
    if unp.empty:
        print("Nothing unparsed — the crosswalk covers every ad-level name. ✅")
        return

    by = (unp.groupby(["channel", "ad_group"])["spend"].sum()
             .reset_index().sort_values("spend", ascending=False))
    print("Still unparsed, ranked by spend (map the top ones first):\n")
    suggestions: dict = {}
    for _, r in by.iterrows():
        print(f"  ${r['spend']:>10,.0f}  {r['channel']:<16} {r['ad_group']}")
        suggestions[r["ad_group"]] = suggest(r["ad_group"], r["channel"])

    out = ROOT / "config" / "naming_overrides.suggested.yaml"
    out.write_text(
        "# DRAFT suggestions — review/edit, then move good entries into naming_overrides.yaml.\n"
        "# These are heuristic guesses, NOT applied automatically.\n"
        + yaml.safe_dump({"ad_group_overrides": suggestions}, sort_keys=False,
                         allow_unicode=True, width=100),
        encoding="utf-8")
    print(f"\nDraft mappings -> {out.relative_to(ROOT)} (review before use)")


if __name__ == "__main__":
    main()
