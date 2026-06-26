"""Write plain-English commentary on MMM results — with guardrails.

Deliberately reports credible intervals and hedges causal language so the
narrative never over-claims. An optional LLM polish step can be added later
(set ANTHROPIC_API_KEY); the deterministic core below needs no network.
"""
from __future__ import annotations


def _money(x: float) -> str:
    ax = abs(x)
    if ax >= 1e6:
        return f"${x/1e6:.2f}M"
    if ax >= 1e3:
        return f"${x/1e3:.0f}k"
    return f"${x:,.0f}"


def generate_commentary(result, cleaning_report: dict | None = None, target: str = "revenue") -> str:
    s = result.channel_summary
    fm = result.fit_metrics
    L = []
    L.append("# MMM Results — Commentary\n")
    L.append(f"_Engine: **{result.engine}** · {fm['n_obs']} weekly observations · "
             "auto-generated, uncertainty-aware._\n")

    fit_q = "strong" if fm["r2"] >= 0.8 else "moderate" if fm["r2"] >= 0.6 else "weak"
    L.append("## Model fit\n")
    L.append(f"The model explains a **{fit_q}** share of weekly {target} variation "
             f"(in-sample R² = {fm['r2']:.2f}; held-out R² = {fm['test_r2']:.2f}, "
             f"held-out MAPE = {fm['test_mape']*100:.1f}%). Held-out figures are the reliability "
             "guide — they matter more than in-sample fit.\n")

    top = s.iloc[0]
    L.append(f"## What's associated with {target}\n")
    L.append(f"Over the period, paid media is **associated with** an estimated "
             f"{_money(s.contribution.sum())} of {target}. The largest estimated contributor is "
             f"**{top.channel}** (~{_money(top.contribution)}; 90% interval "
             f"{_money(top.contribution_low)}–{_money(top.contribution_high)}).\n")
    L.append("| Channel | Spend | Est. contribution (90% CI) | Est. ROI (90% CI) |")
    L.append("|---|---|---|---|")
    for _, r in s.iterrows():
        L.append(f"| {r.channel} | {_money(r.spend)} | {_money(r.contribution)} "
                 f"({_money(r.contribution_low)}–{_money(r.contribution_high)}) | "
                 f"{r.roi:.2f}x ({r.roi_low:.2f}–{r.roi_high:.2f}) |")
    L.append("")

    L.append("## Efficiency & risk flags\n")
    flags = []
    for _, r in s.iterrows():
        if r.roi_low < 1.0 <= r.roi:
            flags.append(f"- **{r.channel}**: point ROI {r.roi:.2f}x but the interval dips below 1.0 — "
                         "profitability is *not statistically clear*; validate before scaling.")
        elif r.roi_low >= 1.5:
            flags.append(f"- **{r.channel}**: ROI interval sits well above 1.0 "
                         f"({r.roi_low:.2f}–{r.roi_high:.2f}x) — a relatively safe place to lean in.")
        p = result.params.get(r.channel, {})
        ms, hs = p.get("mean_spend"), p.get("half_sat")
        if ms and hs:
            if ms > 1.3 * hs:
                flags.append(f"- **{r.channel}**: average spend is past the saturation midpoint — "
                             "incremental dollars face diminishing returns.")
            elif ms < 0.6 * hs:
                flags.append(f"- **{r.channel}**: spending below the saturation midpoint — likely "
                             "**headroom to scale** before diminishing returns bite.")
    L += flags or ["- No strong flags; estimates are broadly stable."]
    L.append("")

    if cleaning_report:
        cr = cleaning_report
        L.append("## Data quality\n")
        L.append(f"Ingested and cleansed {cr['rows_in']:,} granular rows → {cr['rows_out']:,} clean rows "
                 f"(removed {cr['duplicates_removed']} duplicates, dropped {cr['bad_dates_dropped']} undated rows, "
                 f"clipped {cr['negatives_clipped']} negatives, filled {cr['missing_values_filled']} missing cells).\n")

    L.append("## Important caveats\n")
    L.append("- These are **modeled, correlational estimates** — not proven causation. The baseline and "
             "adstock/saturation shapes absorb confounders imperfectly.")
    L.append("- Every figure carries uncertainty; the **90% intervals** are the honest read, not the point estimates.")
    L.append("- Validate the highest-stakes budget moves with **geo experiments or holdout tests** before reallocating.")
    return "\n".join(L)
