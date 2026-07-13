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


def generate_descriptive_commentary(weekly, cleaning_report: dict | None = None,
                                    kpi_label: str = "key events") -> str:
    """Descriptive (non-causal) commentary for the no-MMM path.

    Used when no business-KPI series exists yet (e.g. a file-drop deployment awaiting
    CRM matchback): reports what each channel DID — spend, delivery, platform-claimed
    conversions vs analytics-measured key events — and explicitly does not attribute.
    """
    d = weekly.copy()
    has_ke = "key_events" in d.columns and d["key_events"].notna().any()
    per = d.groupby("channel")[[c for c in ("spend", "impressions", "clicks",
                                            "conversions", "key_events") if c in d.columns]] \
           .sum(min_count=1)
    paid = per[per["spend"] > 0].sort_values("spend", ascending=False)
    nonpaid = per[per["spend"].fillna(0) <= 0]

    L = ["# Campaign Performance — Descriptive Commentary\n"]
    L.append("_No incrementality model yet (business-KPI series pending — e.g. CRM "
             "matchback). Everything below is **descriptive**: what happened, per "
             "platform's own reporting and analytics. No causal claims._\n")

    total_spend = float(paid["spend"].sum())
    L.append("## Paid channels\n")
    L.append(f"Total spend {_money(total_spend)} across {len(paid)} channels.\n")
    singular = kpi_label[:-1] if kpi_label.endswith("s") else kpi_label
    cols = ["Channel", "Spend", "Clicks", "CPC", "Platform-claimed conv."]
    if has_ke:
        cols += [f"{kpi_label.title()} (analytics)", f"Cost / {singular}"]
    L.append("| " + " | ".join(cols) + " |")
    L.append("|" + "---|" * len(cols))
    for ch, r in paid.iterrows():
        cpc = r["spend"] / r["clicks"] if r.get("clicks") else float("nan")
        cells = [str(ch), _money(r["spend"]), f"{r.get('clicks', 0):,.0f}",
                 f"${cpc:,.2f}", f"{r.get('conversions', 0):,.0f}"]
        if has_ke:
            ke = r.get("key_events")
            cpk = r["spend"] / ke if ke and ke == ke and ke > 0 else float("nan")
            cells += [f"{ke:,.0f}" if ke == ke else "—",
                      f"${cpk:,.0f}" if cpk == cpk else "—"]
        L.append("| " + " | ".join(cells) + " |")
    L.append("")

    if has_ke:
        claimed = float(paid["conversions"].sum())
        measured = float(paid["key_events"].sum(min_count=1) or float("nan"))
        if claimed and measured == measured:
            L.append("## Platform claims vs analytics measurement\n")
            L.append(f"Ad platforms collectively claim **{claimed:,.0f} conversions**; "
                     f"analytics measures **{measured:,.0f} {kpi_label}** on the same "
                     f"campaigns ({claimed / measured:.1f}x). Platforms self-attribute "
                     "(view-through, modeled, overlapping credit), so the gap is expected "
                     "— treat platform conversion counts as directional, analytics as the "
                     "consistent yardstick, and neither as proof of incrementality.\n")

    if not nonpaid.empty and has_ke:
        L.append("## Non-paid traffic (baseline context)\n")
        for ch, r in nonpaid.iterrows():
            ke = r.get("key_events")
            if ke == ke:
                L.append(f"- **{ch}**: {ke:,.0f} {kpi_label}")
        L.append("")

    if cleaning_report:
        cr = cleaning_report
        L.append("## Data quality\n")
        L.append(f"Ingested and cleansed {cr['rows_in']:,} rows → {cr['rows_out']:,} "
                 f"({cr['duplicates_removed']} duplicates, {cr['negatives_clipped']} "
                 f"negatives clipped, {cr['missing_values_filled']} missing filled).\n")

    L.append("## What's needed for incrementality\n")
    L.append("- A weekly business-KPI series (CRM matchback: applications started/"
             "submitted) unlocks the MMM — modeled contribution per channel with "
             "uncertainty intervals, instead of platform self-attribution.")
    L.append("- Until then, budget decisions from this report should lean on cost "
             "efficiency and analytics-measured outcomes, validated with holdout tests "
             "where stakes are high.")
    return "\n".join(L)


def _count(x: float) -> str:
    return f"{x:,.0f}"


def _count_commentary(result, s, fm, cleaning_report, outcome, good, warn, _cpo) -> str:
    """MMM commentary for a COUNT target (e.g. submitted applications): contribution in
    outcomes, cost PER incremental outcome graded against the client band, hedged."""
    L = ["# MMM Results — Commentary\n"]
    L.append(f"_Engine: **{result.engine}** · {fm['n_obs']} weekly observations · "
             "auto-generated, uncertainty-aware._\n")
    fit_q = "strong" if fm["test_r2"] >= 0.7 else "moderate" if fm["test_r2"] >= 0.5 else "weak"
    L.append("## Model fit\n")
    L.append(f"The model shows **{fit_q}** held-out accuracy on weekly {outcome} "
             f"(held-out R² = {fm['test_r2']:.2f}, held-out MAPE = {fm['test_mape']*100:.1f}%; "
             f"in-sample R² = {fm['r2']:.2f}). Held-out figures are the reliability guide.\n")
    if fm["r2"] - fm["test_r2"] > 0.15:
        L.append(f"⚠️ The in-sample/held-out gap ({fm['r2']:.2f} vs {fm['test_r2']:.2f}) "
                 "suggests some overfitting — treat channel-level estimates with caution.\n")

    total_c = s.contribution.sum()
    top = s.iloc[0]
    L.append(f"## What drives {outcome}\n")
    L.append(f"Over the period, paid media is **associated with** an estimated "
             f"**{_count(total_c)} incremental {outcome}**. The MMM target is *submitted "
             f"applications* — media buys applications; it cannot pass a polygraph, so the "
             "post-submission pipeline (screening → offer) is downstream and not modeled. "
             f"The largest estimated contributor is **{top.channel}** "
             f"(~{_count(top.contribution)}; 90% interval "
             f"{_count(top.contribution_low)}–{_count(top.contribution_high)}).\n")
    L.append(f"Channels are graded on **cost per incremental {outcome}** against the client "
             f"target (good ≤ ${good:,.0f}, watch ≤ ${warn:,.0f}) — ROI-vs-1.0 is meaningless "
             "for a count target.\n")
    L.append(f"| Channel | Spend | Est. incremental {outcome} (90% CI) | Cost / incremental (90% CI) |")
    L.append("|---|---|---|---|")
    for _, r in s.iterrows():
        cp, cp_lo, cp_hi = _cpo(r.spend, r.contribution), _cpo(r.spend, r.contribution_high), _cpo(r.spend, r.contribution_low)
        cp_s = "n/a" if cp == float("inf") else _money(cp)
        hi_s = "∞" if cp_hi == float("inf") else _money(cp_hi)
        L.append(f"| {r.channel} | {_money(r.spend)} | {_count(r.contribution)} "
                 f"({_count(r.contribution_low)}–{_count(r.contribution_high)}) | "
                 f"{cp_s} ({_money(cp_lo)}–{hi_s}) |")
    L.append("")

    L.append("## Efficiency & risk flags\n")
    flags = []
    for _, r in s.iterrows():
        cp_lo, cp_hi = _cpo(r.spend, r.contribution_high), _cpo(r.spend, r.contribution_low)
        if cp_hi == float("inf"):
            flags.append(f"- **{r.channel}**: the model can't rule out **zero** incremental "
                         f"{outcome} (cost interval {_money(cp_lo)}–∞) — **unproven**; too "
                         "small or too collinear to identify. Validate with a holdout before "
                         "any call.")
        elif cp_hi <= good:
            flags.append(f"- **{r.channel}**: cost per incremental {outcome} sits entirely "
                         f"below the ${good:,.0f} target ({_money(cp_lo)}–{_money(cp_hi)}) — "
                         "**strong**, a relatively safe place to lean in.")
        elif cp_lo > warn:
            flags.append(f"- **{r.channel}**: even the best-case cost ({_money(cp_lo)}) is "
                         f"above the ${warn:,.0f} watch line — a **cut candidate**; "
                         "restructure or reallocate.")
        else:
            flags.append(f"- **{r.channel}**: cost per incremental {outcome} interval "
                         f"({_money(cp_lo)}–{_money(cp_hi)}) straddles the client band — "
                         "**unproven**; the model can't confidently place it. Validate first.")
        p = result.params.get(r.channel, {})
        ms, hs = p.get("mean_spend"), p.get("half_sat")
        if ms and hs and ms > 1.3 * hs:
            flags.append(f"- **{r.channel}**: average spend is past the saturation midpoint — "
                         "incremental dollars face diminishing returns.")
    L += flags or ["- No flags triggered at current thresholds."]
    L.append("")

    if cleaning_report:
        cr = cleaning_report
        L.append("## Data quality\n")
        L.append(f"Ingested and cleansed {cr['rows_in']:,} granular rows → {cr['rows_out']:,} "
                 f"clean rows (removed {cr['duplicates_removed']} duplicates, dropped "
                 f"{cr['bad_dates_dropped']} undated rows, clipped {cr['negatives_clipped']} "
                 f"negatives, filled {cr['missing_values_filled']} missing cells).\n")

    L.append("## Important caveats\n")
    L.append("- These are **modeled, correlational estimates** — not proven causation. "
             "Baseline and adstock/saturation shapes absorb confounders imperfectly.")
    L.append("- Every figure carries uncertainty; the **90% intervals** are the honest read.")
    L.append("- Collinear or small-spend channels show wide intervals **by design** — the "
             "model honestly can't separate them; Bayesian priors (Meridian) narrow these.")
    L.append("- Validate the highest-stakes budget moves with **geo experiments or holdout "
             "tests** before reallocating.")
    return "\n".join(L)


def generate_commentary(result, cleaning_report: dict | None = None, target: str = "revenue",
                        *, target_kind: str = "currency", cost_band: dict | None = None,
                        kpi_label: str | None = None) -> str:
    s = result.channel_summary
    fm = result.fit_metrics
    is_count = str(target_kind).lower() == "count"
    outcome = (kpi_label or target.replace("_", " ")) if is_count else target
    band = cost_band or {"good": 400.0, "warn": 650.0}
    good, warn = float(band.get("good", 400)), float(band.get("warn", 650))

    def _cpo(spend, contrib):     # $ per incremental outcome (inf if no measurable effect)
        return spend / contrib if contrib and contrib > 1.0 else float("inf")

    if is_count:
        return _count_commentary(result, s, fm, cleaning_report, outcome, good, warn, _cpo)
    L = []
    L.append("# MMM Results — Commentary\n")
    L.append(f"_Engine: **{result.engine}** · {fm['n_obs']} weekly observations · "
             "auto-generated, uncertainty-aware._\n")

    # fit quality is judged on the HELD-OUT R² — the same sentence tells readers holdout
    # matters more, so the adjective must not be keyed to the in-sample figure
    fit_q = "strong" if fm["test_r2"] >= 0.7 else "moderate" if fm["test_r2"] >= 0.5 else "weak"
    L.append("## Model fit\n")
    L.append(f"The model shows **{fit_q}** held-out accuracy on weekly {target} "
             f"(held-out R² = {fm['test_r2']:.2f}, held-out MAPE = {fm['test_mape']*100:.1f}%; "
             f"in-sample R² = {fm['r2']:.2f}). Held-out figures are the reliability "
             "guide — they matter more than in-sample fit.\n")
    if fm["r2"] - fm["test_r2"] > 0.15:
        L.append(f"⚠️ The in-sample/held-out gap ({fm['r2']:.2f} vs {fm['test_r2']:.2f}) "
                 "suggests some overfitting — treat channel-level estimates with extra caution.\n")

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
        # all four ROI outcomes are covered — the worst case (confidently unprofitable)
        # used to fall through every branch and print "No strong flags"
        if r.roi_high < 1.0:
            flags.append(f"- **{r.channel}**: the entire ROI interval sits below 1.0 "
                         f"({r.roi_low:.2f}–{r.roi_high:.2f}x) — this channel is **losing money "
                         "with statistical confidence**; cut or restructure before anything else.")
        elif r.roi_low < 1.0 <= r.roi:
            flags.append(f"- **{r.channel}**: point ROI {r.roi:.2f}x but the interval dips below 1.0 — "
                         "profitability is *not statistically clear*; validate before scaling.")
        elif r.roi < 1.0:  # point below breakeven, interval straddles 1.0
            flags.append(f"- **{r.channel}**: point ROI {r.roi:.2f}x is below breakeven and the "
                         f"interval straddles 1.0 ({r.roi_low:.2f}–{r.roi_high:.2f}x) — "
                         "profitability is *unproven*; do not scale without a test.")
        elif r.roi_low >= 1.5:
            flags.append(f"- **{r.channel}**: ROI interval sits well above 1.0 "
                         f"({r.roi_low:.2f}–{r.roi_high:.2f}x) — a relatively safe place to lean in.")
        p = result.params.get(r.channel, {})
        ms, hs = p.get("mean_spend"), p.get("half_sat")
        if ms and hs:
            if ms > 1.3 * hs:
                flags.append(f"- **{r.channel}**: average spend is past the saturation midpoint — "
                             "incremental dollars face diminishing returns.")
            elif ms < 0.6 * hs and r.roi_high >= 1.0:
                # never advertise "headroom to scale" for a channel that may be unprofitable
                flags.append(f"- **{r.channel}**: spending below the saturation midpoint — likely "
                             "**headroom to scale** before diminishing returns bite.")
    L += flags or ["- No flags triggered at current thresholds."]
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
