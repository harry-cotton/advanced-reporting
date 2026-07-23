"""Build the single-file HTML client report (no model calls — deterministic).

    python scripts/build_report.py [--allow-unconfirmed] [--audience internal]

Embeds the report spec's framing and the published AI commentary when current;
works fine with neither (deterministic defaults, honest note in the AI section).
Refuses while report framing is unconfirmed/invalid for the loaded data (confirm
on the dashboard Setup page); --allow-unconfirmed builds a watermarked DRAFT.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from advanced_reporting.reporting.framing import FramingError  # noqa: E402
from advanced_reporting.reporting.html_report import build_report  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build the HTML client report.")
    ap.add_argument("--allow-unconfirmed", action="store_true",
                    help="build a DRAFT-watermarked report even while framing is "
                         "unconfirmed/invalid (never for client delivery)")
    ap.add_argument("--audience", default=None, choices=("client", "internal"),
                    help="override reporting.report_audience for this build")
    args = ap.parse_args()
    try:
        out = build_report(ROOT, audience=args.audience,
                           allow_unconfirmed=args.allow_unconfirmed)
    except FramingError as e:
        print(e)
        sys.exit(2)
    size_kb = out.stat().st_size / 1024
    print(f"client report -> {out.relative_to(ROOT)} ({size_kb:,.0f} KB, "
          "self-contained — email or open anywhere)")
    if args.allow_unconfirmed and "DRAFT" in out.read_text(encoding="utf-8")[:400]:
        print("DRAFT watermark applied — framing unconfirmed; confirm on the "
              "dashboard Setup page before client delivery.")
