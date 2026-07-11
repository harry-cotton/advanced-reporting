"""Run the agent system's pipeline-time advisors (AGENT_SYSTEM_BRIEF.md).

    python scripts/advise.py --spec         # A1: outputs/report_spec.json
    python scripts/advise.py --commentary   # A2: outputs/commentary_ai.md (guarded)
    python scripts/advise.py --spec --commentary   # both, spec first

Requires processed data (run_pipeline.py first) and an ANTHROPIC_API_KEY; without
either it explains itself and exits — the dashboard keeps working on deterministic
defaults, unchanged. A commentary draft that fails the number guard is REJECTED
and never written; the violations are printed instead.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from advanced_reporting.agent import generate_spec  # noqa: E402
from advanced_reporting.agent.commentary_agent import (  # noqa: E402
    COMMENTARY_PATH, generate_commentary)
from advanced_reporting.agent.spec_agent import SPEC_PATH  # noqa: E402


def run_spec(model: str | None) -> int:
    spec, info = generate_spec(ROOT, model=model)
    if spec is None:
        print(f"spec agent did not run: {info.get('error')}")
        return 1
    print(f"report spec -> {SPEC_PATH}")
    for key in ("campaign_type", "primary_tier", "kpi_label"):
        if key in spec:
            print(f"  {key}: {spec[key]}")
    if spec.get("blocks"):
        print(f"  blocks: {' -> '.join(spec['blocks'])}")
    if spec.get("targets"):
        print(f"  targets: {', '.join(sorted(spec['targets']))}")
    for flag in spec.get("watch_flags", []):
        print(f"  watch: {flag}")
    for d in info.get("dropped", []):
        print(f"  DROPPED (fell back to defaults): {d}")
    cost = info.get("cost_usd")
    print(f"  model={info.get('model')}  tokens={info.get('input_tokens')}in/"
          f"{info.get('output_tokens')}out"
          + (f"  cost=${cost:.4f}" if cost is not None else ""))
    return 0


def run_commentary(model: str | None) -> int:
    body, info = generate_commentary(ROOT, model=model)
    if body is None:
        print(f"commentary agent did not publish: {info.get('error')}")
        for v in info.get("violations", []):
            print(f"  GUARD: {v}")
        return 1
    print(f"AI commentary -> {COMMENTARY_PATH} (stamped; dashboard shows it only "
          "when reporting.ai_commentary is true)")
    for d in info.get("dropped", []):
        print(f"  DROPPED: {d}")
    cost = info.get("cost_usd")
    print(f"  model={info.get('model')}  tokens={info.get('input_tokens')}in/"
          f"{info.get('output_tokens')}out"
          + (f"  cost=${cost:.4f}" if cost is not None else ""))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Pipeline-time agent advisors.")
    ap.add_argument("--spec", action="store_true",
                    help="run the report-spec agent (A1) -> outputs/report_spec.json")
    ap.add_argument("--commentary", action="store_true",
                    help="run the commentary agent (A2) -> outputs/commentary_ai.md")
    ap.add_argument("--model", default=None,
                    help="override the model id (default: config agent.model)")
    args = ap.parse_args()
    if not (args.spec or args.commentary):
        ap.error("nothing to do — pass --spec and/or --commentary")
    rc = 0
    if args.spec:
        rc = run_spec(args.model)
    if args.commentary:
        rc = max(rc, run_commentary(args.model))
    raise SystemExit(rc)
