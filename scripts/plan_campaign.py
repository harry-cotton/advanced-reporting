"""Plan a campaign end-to-end: store + fitted MMM -> CampaignPlan -> naming generator.

Deterministic by default (no key needed). With ANTHROPIC_API_KEY set (or --use-llm) the
qualitative selection comes from one guarded LLM call; budgets always come from the allocator.
Writes outputs/campaign_plan.json (+ budgets/trace), outputs/campaign_plan.xlsx (generator-ready),
and — unless --no-names — runs the generator to outputs/trafficking_sheet.xlsx.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))  # for the root-level naming package

from advanced_reporting.utils import load_config
from advanced_reporting.planner import plan_campaign, load_rails, write_plan_xlsx


def _fit_mmm(cfg):
    """Return ``(history_df, mmm_result)`` from the durable store, or ``(None, None)``."""
    from advanced_reporting.ingestion.csv_source import CSVSource
    from advanced_reporting.transform.clean import (
        load_history, clean_ad_data, to_weekly, build_modeling_table)
    from advanced_reporting.mmm.factory import get_engine

    m = cfg["modeling"]
    ad_clean, _ = clean_ad_data(load_history())
    weekly = to_weekly(ad_clean)
    kpi = CSVSource(ROOT / "data/raw/business_kpi_weekly.csv", "kpi").fetch()
    model_df = build_modeling_table(weekly, kpi, m["channel_spend_cols"],
                                    m["control_cols"], m["target"])
    engine = get_engine(m["engine"], train_frac=m.get("train_frac", 0.85),
                        adstock_max_lag=m.get("adstock_max_lag", 8))
    result = engine.fit(model_df, m["channel_spend_cols"], m["control_cols"],
                        m["target"], m["date_col"])
    return ad_clean, result


def run(goal, budget, *, market=None, campaign=None, primary_kpi="conversions",
        use_llm=None, write_names=True, weeks=None):
    cfg = load_config()
    outdir = ROOT / cfg["reporting"]["output_dir"]
    outdir.mkdir(parents=True, exist_ok=True)
    rails = load_rails()

    history, mmm_result = None, None
    try:
        history, mmm_result = _fit_mmm(cfg)
    except FileNotFoundError:
        print("  (no durable store yet — using rules budget split; run scripts/ingest.py first "
              "for incrementality-grounded allocation)")

    goals = {
        "goal": goal, "total_budget": float(budget), "n_weeks": weeks,
        "market": market or rails["campaign"].get("default_market", "US"),
        "campaign": campaign or f"{goal}_campaign", "primary_kpi": primary_kpi,
    }
    plan = plan_campaign(goals, rails, history=history, mmm_result=mmm_result, use_llm=use_llm)

    (outdir / "campaign_plan.json").write_text(
        json.dumps({"plan": plan.to_dict(), "budgets": plan.to_budget_table()},
                   indent=2, default=str), encoding="utf-8")
    xlsx = outdir / "campaign_plan.xlsx"
    write_plan_xlsx(plan, xlsx)

    print("Campaign plan complete.")
    print(f"  goal={plan.trace.choice.get('goal', goal)}  budget=${plan.total_budget:,.0f}  "
          f"source={plan.trace.source}  confidence={plan.trace.confidence}")
    if plan.trace.source == "llm":
        print(f"  LLM model={plan.trace.model}  tokens in/out={plan.trace.input_tokens}/"
              f"{plan.trace.output_tokens}  cost=${plan.trace.cost_usd:.4f}")
    for st in plan.stages:
        for pf in st.platforms:
            print(f"  {st.objective:>10} | {pf.channel:<14} ${pf.budget:,.0f}")
    print(f"  -> {outdir / 'campaign_plan.json'}")
    print(f"  -> {xlsx}")

    if write_names:
        from naming import naming_generator
        out = outdir / "trafficking_sheet.xlsx"
        naming_generator.generate(str(xlsx), str(out))
        print(f"  -> {out}")
    return plan


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Plan a campaign from goals + rails.")
    ap.add_argument("--goal", default="conversion",
                    help="awareness | consideration | conversion (or a campaign name to infer)")
    ap.add_argument("--budget", type=float, default=100_000.0, help="total budget")
    ap.add_argument("--market", default=None)
    ap.add_argument("--campaign", default=None)
    ap.add_argument("--kpi", default="conversions", help="primary KPI")
    ap.add_argument("--weeks", type=float, default=None,
                    help="flight length in weeks (response curves are weekly; assumed 4 if omitted)")
    ap.add_argument("--use-llm", action="store_true",
                    help="force the guarded LLM path (default: on iff ANTHROPIC_API_KEY set)")
    ap.add_argument("--no-names", action="store_true", help="skip running the naming generator")
    a = ap.parse_args()
    run(a.goal, a.budget, market=a.market, campaign=a.campaign, primary_kpi=a.kpi,
        use_llm=(True if a.use_llm else None), write_names=not a.no_names, weeks=a.weeks)
