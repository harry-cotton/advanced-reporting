"""Build the single-file HTML client report (no model calls — deterministic).

    python scripts/build_report.py

Embeds the report spec's framing and the published AI commentary when current;
works fine with neither (deterministic defaults, honest note in the AI section).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from advanced_reporting.reporting.html_report import build_report  # noqa: E402

if __name__ == "__main__":
    out = build_report(ROOT)
    size_kb = out.stat().st_size / 1024
    print(f"client report -> {out.relative_to(ROOT)} ({size_kb:,.0f} KB, "
          "self-contained — email or open anywhere)")
