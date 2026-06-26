"""Generate ~2 years of synthetic, granular marketing data with a known ground-truth DGP.

The DGP itself lives in ``advanced_reporting.ingestion.synthetic`` (single source of truth,
also used by ``SyntheticSource``), so the on-disk CSVs and the live synthetic source never
drift. This script just runs that DGP with the default seed/geos and writes the files.

Writes:
  data/raw/ad_platform_daily.csv   granular, slightly messy per-campaign-per-geo daily rows
  data/raw/business_kpi_weekly.csv weekly business revenue + control variables
  outputs/ground_truth.json        the true per-channel contribution & ROI (for validation)
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from advanced_reporting.ingestion.synthetic import (  # noqa: E402
    DEFAULT_GEOS, N_WEEKS, simulate_weekly, build_ad_frame, build_kpi_frame)


def main() -> None:
    rng = np.random.default_rng(42)
    # Order matches SyntheticSource (simulate -> ad) so the ad CSV equals SyntheticSource
    # output for the same seed/geos; the KPI is built afterwards from the same contribution.
    weeks, t, spend_wk, contrib_wk, truth = simulate_weekly(rng)
    ad = build_ad_frame(weeks, spend_wk, DEFAULT_GEOS, rng, messy=True)
    kpi = build_kpi_frame(weeks, t, contrib_wk, rng)

    (ROOT / "data/raw").mkdir(parents=True, exist_ok=True)
    (ROOT / "outputs").mkdir(parents=True, exist_ok=True)
    ad.to_csv(ROOT / "data/raw/ad_platform_daily.csv", index=False)
    kpi.to_csv(ROOT / "data/raw/business_kpi_weekly.csv", index=False)
    json.dump({"weeks": N_WEEKS, "channels": truth},
              open(ROOT / "outputs/ground_truth.json", "w"), indent=2)

    revenue_total = kpi["revenue"].sum()
    print(f"Wrote {len(ad):,} granular ad rows across {ad['date'].nunique()} days "
          f"and {ad['geo'].nunique()} geos")
    print(f"Wrote {len(kpi)} weekly KPI rows  |  revenue ${revenue_total/1e6:.1f}M total")
    print("Ground-truth ROI by channel:")
    for ch, v in truth.items():
        print(f"  {ch:<14} ROI {v['roi']:.2f}x  spend ${v['total_spend']/1e6:.2f}M")


if __name__ == "__main__":
    main()
