"""Deterministic insight blocks for the narrative Overview page (redesign R2).

Every block is COMPUTED from the weekly tables — top mover, efficiency gap, claim
ratio, pacing — and woven into template prose over those computed facts. No LLM, no
fabricated commentary: if a block's inputs are missing (e.g. no GA4 key events yet)
the block either degrades with honest labels ("platform-claimed") or returns None and
the page simply doesn't show it.

Each function returns a dict: ``title`` (the chart's ACTION TITLE — an insight
sentence), ``narrative`` (a woven paragraph), plus the frame(s) the chart needs.
Pure pandas in/out so the whole narrative layer is unit-testable without Streamlit.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..reporting import metrics as M
from ..reporting.metrics import format_value as _fmt

NONPAID_LABEL = "Organic & direct"

# Presentation names for channel keys — used in prose and by theme.py for charts.
# (Raw keys like ``google_search`` also trip Markdown's underscore emphasis, so prose
# must always go through ``channel_label``.)
CHANNEL_LABELS = {
    "google_search": "Google Search",
    "google_demandgen": "Google Demand Gen",
    "google_pmax": "Google PMax",
    "meta": "Meta",
    "linkedin": "LinkedIn",
    "tiktok": "TikTok",
    "organic_search": "Organic search",
    "direct": "Direct",
}


def channel_label(channel: str) -> str:
    return CHANNEL_LABELS.get(channel, str(channel).replace("_", " ").title())


def _money(x) -> str:
    return _fmt(x, "currency")


def _singular(label: str) -> str:
    return label[:-1] if label.endswith("s") else label


def _a(noun: str) -> str:
    """'a'/'an' + noun ('an application start', 'a key event')."""
    return f"an {noun}" if noun[:1].lower() in "aeiou" else f"a {noun}"


def _paid_channels(weekly: pd.DataFrame) -> list[str]:
    per = weekly.groupby("channel")["spend"].sum()
    return sorted(per[per > 0].index)


def _has_measured(weekly: pd.DataFrame) -> bool:
    return "key_events" in weekly.columns and weekly["key_events"].notna().any()


def _trend_phrase(pct: float) -> str:
    if pd.isna(pct):
        return "holding steady"
    if pct >= 0.03:
        return f"up {pct * 100:.0f}%"
    if pct <= -0.03:
        return f"down {abs(pct) * 100:.0f}%"
    return "holding steady"


def _recent_vs_prior(series: pd.Series, window: int = 4) -> float:
    """Pct change of the trailing ``window`` weeks vs the ``window`` before them."""
    s = series.dropna()
    if len(s) < 2 * window:
        window = max(len(s) // 2, 1)
    recent, prior = s.iloc[-window:].sum(), s.iloc[-2 * window:-window].sum()
    return (recent - prior) / prior if prior else float("nan")


# ---------------------------------------------------------------- headline tile row
def headline_tiles(weekly: pd.DataFrame, kpi_label: str = "key events") -> list[dict]:
    """Executive KPI tiles with 4-week deltas (design feedback: Grow-style top row).

    Each tile: ``{"label", "value", "delta", "delta_color", "help"}`` — ``delta`` is a
    ready-formatted "±N% vs prior 4 wks" string (None when there's no prior period);
    ``delta_color`` is "inverse" for costs (down = good).
    """
    measured = _has_measured(weekly)
    col = "key_events" if measured else "conversions"
    out_label = kpi_label if measured else "claimed conversions"
    paid = weekly[weekly["channel"].isin(_paid_channels(weekly))]

    def _delta(series: pd.Series) -> str | None:
        pct = _recent_vs_prior(series)
        return None if pd.isna(pct) else f"{pct * 100:+.0f}% vs prior 4 wks"

    spend_w = paid.groupby("date")["spend"].sum().sort_index()
    out_w = paid.groupby("date")[col].sum(min_count=1).sort_index()
    total_out = float(out_w.sum())

    tiles = [
        {"label": "Spend", "value": _money(spend_w.sum()), "delta": _delta(spend_w),
         "delta_color": "off", "help": "Paid media spend over the reporting period."},
        {"label": out_label.capitalize(), "value": f"{total_out:,.0f}",
         "delta": _delta(out_w), "delta_color": "normal",
         "help": ("Analytics-measured (GA4) outcomes on paid campaigns." if measured
                  else "Platform-claimed conversions — no analytics series yet.")},
    ]
    if total_out > 0:
        cost_w = (spend_w / out_w).replace([float("inf")], float("nan"))
        tiles.append({
            "label": f"Cost / {_singular(out_label)}",
            "value": _money(float(spend_w.sum()) / total_out),
            "delta": _delta(cost_w.dropna()), "delta_color": "inverse",
            "help": "Total paid spend over total outcomes; lower is better."})
    if measured and "conversions" in weekly.columns:
        claimed = float(paid["conversions"].sum())
        if claimed > 0 and total_out > 0:
            tiles.append({
                "label": "Claim ratio", "value": f"{claimed / total_out:.1f}x",
                "delta": None, "delta_color": "off",
                "help": ("Platform-claimed conversions vs analytics-measured "
                         f"{kpi_label}. Platforms self-attribute; a gap is expected.")})
    # an all-zero sessions column means "not measured" (gap-filled), not "zero
    # traffic" — same convention as the tier scorecard: omit, never show 0
    if ("sessions" in weekly.columns and weekly["sessions"].notna().any()
            and float(weekly["sessions"].fillna(0).sum()) > 0):
        sess_w = weekly.groupby("date")["sessions"].sum(min_count=1).sort_index()
        tiles.append({
            "label": "Sessions", "value": f"{float(sess_w.sum()):,.0f}",
            "delta": _delta(sess_w), "delta_color": "normal",
            "help": "Site sessions (all traffic, incl. organic and direct)."})
    return tiles


def spend_mix(weekly: pd.DataFrame) -> pd.DataFrame:
    """Paid spend share by channel (for the compact mix donut)."""
    paid = weekly[weekly["channel"].isin(_paid_channels(weekly))]
    per = paid.groupby("channel")["spend"].sum().sort_values(ascending=False)
    out = per.reset_index()
    out["share"] = out["spend"] / out["spend"].sum()
    return out


# ---------------------------------------------------------------- tier scorecard (gauges)
# The Awareness/Engagement/Action framing from the Power BI dashboard, mapped onto the
# KPI-pyramid tiers (reach/intent/outcome). Each tier gets a small strip of goal gauges:
# VOLUME metrics pace toward a configured goal (or fall to the totals grid when none is
# set); EFFICIENCY/QUALITY metrics get a RAG bullet graded against configured thresholds
# — or, absent thresholds, against the channel spread (honestly labeled, never a faked
# absolute target). Pure pandas: computed off metrics.compute_metrics, unit-testable.
TIER_LABELS = {"reach": "Awareness", "intent": "Engagement", "outcome": "Action"}

# Per tier: metrics shown as graded RAG bullets vs the volumes shown as pacing/totals.
_TIER_RAG = {
    "reach": ["cpm", "cpc", "ctr"],
    "intent": ["cost_per_session", "engagement_rate", "pages_per_session"],
    "outcome": ["cost_per_key_event", "roas", "conversion_rate"],
}
_TIER_VOLUME = {
    "reach": ["impressions", "clicks"],
    "intent": ["sessions", "video_views"],
    "outcome": ["key_events", "revenue"],
}


def _rag_gauge(value: float, higher_is_better: bool, good: float | None = None,
               warn: float | None = None, sample: list[float] | None = None) -> dict | None:
    """Place ``value`` on a good/amber/bad track, returning marker pos + band stops.

    Absolute mode when both ``good``/``warn`` thresholds are given; otherwise a relative
    mode deriving the bands from the 33rd/66th percentiles of ``sample`` (the per-channel
    values). Returns None if the value is NaN, or relative mode lacks ≥2 sample points.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    mode = "absolute" if (good is not None and warn is not None) else "relative"
    if mode == "relative":
        s = [float(v) for v in (sample or []) if v is not None and not pd.isna(v)]
        if len(s) < 2:
            return None
        lo, hi = float(np.percentile(s, 33)), float(np.percentile(s, 66))
        warn, good = (lo, hi) if higher_is_better else (hi, lo)  # good is the "nice" end

    if higher_is_better:                       # high = good; bad → warn → good, L→R
        scale = max(value, good, warn) * 1.15 or 1.0
        w0, g0 = warn / scale, good / scale
        band_stops = [(0.0, w0, "bad"), (w0, g0, "warn"), (g0, 1.0, "good")]
        verdict = "good" if value >= good else ("warn" if value >= warn else "bad")
    else:                                      # low = good (cost); good → warn → bad, L→R
        scale = max(value, good, warn) * 1.35 or 1.0
        g0, w0 = good / scale, warn / scale
        band_stops = [(0.0, g0, "good"), (g0, w0, "warn"), (w0, 1.0, "bad")]
        verdict = "good" if value <= good else ("warn" if value <= warn else "bad")
    return {"pos": min(max(value / scale, 0.0), 1.0), "band_stops": band_stops,
            "verdict": verdict, "mode": mode}


def tier_scorecard(weekly: pd.DataFrame, tier: str, targets: dict | None = None,
                   kpi_label: str = "key events") -> dict:
    """Goal-gauge scorecard for one pyramid tier (the Awareness/Engagement/Action band).

    Returns ``{tier, label, pace[], rag[], grid[], measured, relative_bands}``:
    ``pace`` = pacing bullets (volume metrics with a configured goal), ``rag`` = graded
    efficiency/quality bullets, ``grid`` = the totals card (Spend + un-goaled volumes).
    """
    targets = targets or {}
    reg = M.load_metric_registry()
    nat = {r["metric"]: r for r in M.compute_metrics(weekly, by=None, registry=reg)
           .to_dict("records")}
    paid = weekly[weekly["channel"].isin(_paid_channels(weekly))]
    per = (M.compute_metrics(paid, by="channel", registry=reg)
           if not paid.empty else None)

    rag_keys = list(_TIER_RAG.get(tier, []))
    vol_keys = list(_TIER_VOLUME.get(tier, []))
    measured = _has_measured(weekly)
    if tier == "outcome" and not measured:     # no GA4 series → fall back to claimed/CPA
        rag_keys = ["cpa" if k == "cost_per_key_event" else k for k in rag_keys]
        vol_keys = ["conversions" if k == "key_events" else k for k in vol_keys]

    rag = []
    for key in rag_keys:
        rec = nat.get(key)
        # An exact 0 here means the underlying column is unpopulated (e.g. page_views /
        # video_views aren't aggregated yet) — skip rather than paint a misleading verdict.
        if rec is None or pd.isna(rec["value"]) or float(rec["value"]) == 0.0:
            continue
        t = targets.get(key, {}) or {}
        sample = ([r["value"] for r in per.to_dict("records") if r["metric"] == key]
                  if per is not None else None)
        g = _rag_gauge(rec["value"], rec["higher_is_better"],
                       good=t.get("good"), warn=t.get("warn"), sample=sample)
        if g is None:
            continue
        rag.append({"key": key, "label": rec["label"],
                    "value_str": _fmt(rec["value"], rec["format"]), **g})

    pace, grid = [], []
    grid.append(("Spend", _money(float(paid["spend"].sum()))))
    for key in vol_keys:
        rec = nat.get(key)
        if rec is None or pd.isna(rec["value"]) or float(rec["value"]) == 0.0:
            continue                           # unpopulated column → omit, don't show "0"
        val, vstr = float(rec["value"]), _fmt(rec["value"], rec["format"])
        goal = (targets.get(key, {}) or {}).get("goal")
        if goal and goal > 0:
            scale = max(val, float(goal))
            pace.append({"key": key, "label": rec["label"], "value_str": vstr,
                         "fill_frac": val / scale, "goal_frac": float(goal) / scale,
                         "pct": val / float(goal),
                         "note": f"{val / float(goal) * 100:.0f}% of "
                                 f"{_fmt(float(goal), rec['format'])} goal"})
        else:
            grid.append((rec["label"], vstr))

    return {"tier": tier, "label": TIER_LABELS.get(tier, tier.title()),
            "pace": pace, "rag": rag, "grid": grid, "measured": measured,
            "relative_bands": any(r["mode"] == "relative" for r in rag)}


# ---------------------------------------------------------------- block 1: KPI trend
def kpi_trend_insight(weekly: pd.DataFrame, kpi_label: str = "key events") -> dict | None:
    """Headline outcome + trend: measured (GA4) when available, else claimed w/ label."""
    measured = _has_measured(weekly)
    col = "key_events" if measured else "conversions"
    label = kpi_label if measured else "platform-claimed conversions"
    if col not in weekly.columns or not weekly[col].notna().any():
        return None

    paid = _paid_channels(weekly)
    d = weekly.copy()
    d["bucket"] = d["channel"].map(lambda c: "Paid media" if c in paid else NONPAID_LABEL)
    series = (d.groupby(["date", "bucket"])[col].sum(min_count=1)
                .unstack("bucket").fillna(0.0).sort_index())
    total = series.sum(axis=1)
    total_n = float(total.sum())
    paid_share = (float(series.get("Paid media", pd.Series(dtype=float)).sum()) / total_n
                  if total_n else float("nan"))
    pct = _recent_vs_prior(total)
    peak_date = total.idxmax()

    # top paid channel by the same outcome column
    per_paid = (weekly[weekly["channel"].isin(paid)]
                .groupby("channel")[col].sum(min_count=1).dropna().sort_values())
    top_channel, top_n = (per_paid.index[-1], float(per_paid.iloc[-1])) \
        if len(per_paid) else (None, 0.0)

    title = f"{label.capitalize()} are {_trend_phrase(pct)} month-on-month"
    narrative = (
        f"The period produced **{total_n:,.0f} {label}**"
        + (f", **{paid_share * 100:.0f}%** of them on paid campaigns"
           if paid_share == paid_share else "")
        + f"; the last four weeks are {_trend_phrase(pct)} versus the four before. ")
    if top_channel:
        narrative += (f"**{channel_label(top_channel)}** is the largest paid "
                      f"contributor ({top_n:,.0f} {label}).")
    if not measured:
        narrative += (" _No analytics-measured outcome series yet — these are the "
                      "platforms' own conversion claims; treat them as directional._")
    return {
        "title": title, "narrative": narrative, "series": series,
        "measured": measured, "label": label, "trend_pct": pct,
        "annotations": [(peak_date, float(total.max()),
                         f"peak week: {total.max():,.0f}")],
    }


# ------------------------------------------------ block 2: claims vs measured (signature)
def claims_vs_measured_insight(weekly: pd.DataFrame,
                               kpi_label: str = "key events") -> dict | None:
    """The signature honesty visual: platform-claimed conversions vs GA4-measured."""
    if not _has_measured(weekly) or "conversions" not in weekly.columns:
        return None
    paid = _paid_channels(weekly)
    per = (weekly[weekly["channel"].isin(paid)]
           .groupby("channel")[["conversions", "key_events"]].sum(min_count=1)
           .rename(columns={"conversions": "claimed", "key_events": "measured"})
           .dropna())
    per = per[(per["claimed"] > 0) & (per["measured"] > 0)]
    if per.empty:
        return None
    per["ratio"] = per["claimed"] / per["measured"]
    per = per.sort_values("claimed", ascending=False).reset_index()

    claimed, measured = float(per["claimed"].sum()), float(per["measured"].sum())
    overall = claimed / measured
    worst = per.loc[per["ratio"].idxmax()]
    title = (f"Ad platforms claim {overall:.1f}x the conversions analytics can measure")
    narrative = (
        f"Across paid channels the platforms report **{claimed:,.0f} conversions**; "
        f"analytics measures **{measured:,.0f} {kpi_label}** on the same campaigns — a "
        f"**{overall:.1f}x** gap. **{channel_label(worst['channel'])}** shows the "
        "widest spread "
        f"({worst['ratio']:.1f}x). Platforms self-attribute (view-through, modeled and "
        "overlapping credit), so a gap is expected — treat platform counts as "
        "directional, analytics as the consistent yardstick, and neither as proof of "
        "incrementality.")
    return {"title": title, "narrative": narrative, "per_channel": per,
            "overall_ratio": overall}


# ------------------------------------------------ block 3: cost per outcome by channel
def cost_per_outcome_insight(weekly: pd.DataFrame,
                             kpi_label: str = "key events") -> dict | None:
    """Efficiency ranking: spend per measured outcome (per claimed, honestly labeled,
    when no analytics series exists)."""
    measured = _has_measured(weekly)
    col = "key_events" if measured else "conversions"
    label = _singular(kpi_label) if measured else "platform-claimed conversion"
    if col not in weekly.columns:
        return None
    paid = _paid_channels(weekly)
    per = (weekly[weekly["channel"].isin(paid)]
           .groupby("channel")[["spend", col]].sum(min_count=1).dropna())
    per = per[per[col] > 0]
    if per.empty:
        return None
    per["cost_per"] = per["spend"] / per[col]
    per = per.sort_values("cost_per").reset_index()

    cheap, dear = per.iloc[0], per.iloc[-1]
    if len(per) >= 2 and dear["cost_per"] / cheap["cost_per"] >= 1.15:
        mult = dear["cost_per"] / cheap["cost_per"]
        title = (f"{channel_label(cheap['channel'])} delivers {_a(label)} for "
                 f"{_money(cheap['cost_per'])} — {mult:.1f}x cheaper than "
                 f"{channel_label(dear['channel'])}")
    else:
        title = (f"Paid channels deliver {_a(label)} for about "
                 f"{_money(per['cost_per'].mean())}")
    narrative = (
        "Cost per outcome ranks "
        + ", ".join(f"**{channel_label(r['channel'])}** at {_money(r['cost_per'])}"
                    for _, r in per.iterrows())
        + ". "
        + ("Costs are per analytics-measured outcome — a consistent yardstick across "
           "platforms, though still not proof of incrementality."
           if measured else
           "_Costs are per platform-claimed conversion (no analytics series yet) — "
           "platforms grade their own homework, so compare direction, not absolutes._"))
    return {"title": title, "narrative": narrative, "per_channel": per,
            "measured": measured, "outcome_label": label}


# ---------------------------------------------------------------- block 4: pacing
def pacing_insight(weekly: pd.DataFrame, budget: dict | None = None) -> dict | None:
    """Spend pacing: vs the configured budget when present, else run-rate + projection.

    ``budget`` (config ``reporting.budget``): ``{"total": float, "flight_weeks": int}``.
    """
    if "spend" not in weekly.columns:
        return None
    by_week = weekly.groupby("date")["spend"].sum().sort_index()
    by_week = by_week[by_week.index.notna()]
    if by_week.empty or float(by_week.sum()) <= 0:
        return None
    cum = by_week.cumsum()
    total_spend = float(cum.iloc[-1])
    n_weeks = len(by_week)
    run_rate = float(by_week.iloc[-4:].mean())

    out = {"cumulative": cum, "run_rate": run_rate, "total_spend": total_spend,
           "n_weeks": n_weeks, "budget": None}
    total_budget = float(budget.get("total", 0) or 0) if budget else 0.0
    flight_weeks = int(budget.get("flight_weeks", 0) or 0) if budget else 0
    if total_budget > 0 and flight_weeks > 0:
        pct_spent = total_spend / total_budget
        pct_elapsed = min(n_weeks / flight_weeks, 1.0)
        gap = pct_spent - pct_elapsed
        if abs(gap) < 0.05:
            phrase, verdict = "on plan", "on_plan"
        else:
            phrase = (f"{abs(gap) * 100:.0f} points {'ahead of' if gap > 0 else 'behind'} "
                      "plan")
            verdict = "ahead" if gap > 0 else "behind"
        title = f"Spend is pacing {phrase}"
        narrative = (
            f"**{_money(total_spend)}** of the **{_money(total_budget)}** budget is spent "
            f"(**{pct_spent * 100:.0f}%**) with **{pct_elapsed * 100:.0f}%** of the "
            f"{flight_weeks}-week flight elapsed. The current run rate is "
            f"{_money(run_rate)}/week; at that pace the budget lands at "
            f"{_money(run_rate * flight_weeks)} over the full flight.")
        out.update({"budget": {"total": total_budget, "flight_weeks": flight_weeks,
                               "pct_spent": pct_spent, "pct_elapsed": pct_elapsed,
                               "verdict": verdict}})
    else:
        title = (f"Spend is running at {_money(run_rate)}/week — "
                 f"{_money(total_spend)} over {n_weeks} weeks")
        narrative = (
            f"Cumulative spend is **{_money(total_spend)}** across {n_weeks} weeks; the "
            f"last four weeks averaged **{_money(run_rate)}/week**. _No budget is "
            "configured (`reporting.budget` in config), so pacing is shown as run rate "
            "rather than vs plan._")
    out.update({"title": title, "narrative": narrative})
    return out


# ---------------------------------------------------------------- topline summary
def topline_summary(weekly: pd.DataFrame, kpi_label: str = "key events") -> str:
    """1–3 sentence leadership abstract: spend, outcome trend, top efficiency gap.

    Always returns a non-empty string. Degrades to claimed conversions (honestly
    labeled) when no analytics-measured series exists.
    """
    measured = _has_measured(weekly)
    col = "key_events" if measured else "conversions"
    label = kpi_label if measured else "platform-claimed conversions"

    paid_channels = _paid_channels(weekly)
    paid = weekly[weekly["channel"].isin(paid_channels)]
    total_spend = float(paid["spend"].sum())
    total_out = float(paid[col].sum(min_count=1)) if col in paid.columns else 0.0
    n_ch = len(paid_channels)
    ch_word = f"{n_ch} channel{'s' if n_ch != 1 else ''}"

    pct = float("nan")
    if total_out > 0:
        pct = _recent_vs_prior(paid.groupby("date")[col].sum(min_count=1).sort_index())

    s1 = (f"Paid media spent **{_money(total_spend)}** and delivered "
          f"**{total_out:,.0f} {label}** across {ch_word}"
          + (f", {_trend_phrase(pct)} month-on-month" if not pd.isna(pct) else "")
          + ".")

    s2 = ""
    eff = cost_per_outcome_insight(weekly, kpi_label)
    if eff and len(eff["per_channel"]) >= 2:
        cheap, dear = eff["per_channel"].iloc[0], eff["per_channel"].iloc[-1]
        s2 = (f" **{channel_label(cheap['channel'])}** leads on efficiency at "
              f"**{_money(cheap['cost_per'])}** per {eff['outcome_label']}, "
              f"with **{channel_label(dear['channel'])}** at "
              f"**{_money(dear['cost_per'])}**.")

    s3 = ""
    cv = claims_vs_measured_insight(weekly, kpi_label)   # same ratio as the block below
    if cv:
        s3 = (f" The platforms claim **{cv['overall_ratio']:.1f}×** more conversions "
              f"than analytics can verify — the **{kpi_label}** figures are "
              "the consistent yardstick throughout this report.")

    return s1 + s2 + s3


# ---------------------------------------------------------------- audience callout
def audience_callout_insight(hist: pd.DataFrame) -> dict | None:
    """Best-vs-worst audience efficiency callout for the Overview page.

    Reads history.parquet (ad-level, decoded rows only). Returns None when no
    decoded audience rows exist. All conversions are platform-claimed.
    """
    if "ad_group" not in hist.columns or "audience_type" not in hist.columns:
        return None
    from ..ingestion.naming_decode import UNPARSED
    ad = hist[
        (hist["ad_group"] != "")
        & (hist["audience_type"] != "")
        & (hist["audience_type"] != UNPARSED)
    ]
    if ad.empty:
        return None
    per = (
        ad.groupby(["audience_type", "audience_detail"])[["spend", "conversions"]]
        .sum(min_count=1)
        .dropna()
        .reset_index()
    )
    per = per[(per["conversions"] > 0) & (per["spend"] > 0)].copy()
    if len(per) < 2:
        return None
    per["cost_per_claimed"] = per["spend"] / per["conversions"]
    per = per.sort_values("cost_per_claimed").reset_index(drop=True)

    # Compare WITHIN one audience type only: warm (retargeting) audiences convert
    # cheaper than cold prospecting by construction, so a cross-type "X beats Y"
    # headline would be exactly the misleading claim the Audiences page warns about.
    # Take the type with the widest within-type spread (needs >=2 audiences of a type).
    groups = [g for _, g in per.groupby("audience_type", sort=False) if len(g) >= 2]
    if not groups:
        return None
    grp = max(groups, key=lambda g: (g["cost_per_claimed"].iloc[-1]
                                     / g["cost_per_claimed"].iloc[0]))
    best, worst = grp.iloc[0], grp.iloc[-1]
    mult = worst["cost_per_claimed"] / best["cost_per_claimed"]
    atype = best["audience_type"]

    title = (f"Among {atype} audiences, {best['audience_detail']} converts at "
             f"{mult:.1f}× less cost than {worst['audience_detail']}")
    narrative = (
        f"Within the **{atype}** audiences, **{best['audience_detail']}** delivers "
        f"claimed conversions at **{_money(best['cost_per_claimed'])}** each — "
        f"**{mult:.1f}×** more efficient than **{worst['audience_detail']}** at "
        f"{_money(worst['cost_per_claimed'])}. "
        "_Comparisons stay within one audience type: warm retargeting audiences convert "
        "cheaper than cold prospecting by construction. All audience figures are "
        "platform-claimed; the Audiences page has the full ranking._"
    )
    return {
        "title": title, "narrative": narrative, "per_audience": per,
        "best": best.to_dict(), "worst": worst.to_dict(), "mult": mult,
    }


# ---------------------------------------------------------------- macro slot (hidden)
def macro_context(cfg: dict | None) -> list[str] | None:
    """The DEFERRED "External context" aside: curated per-client notes only.

    Activates only when ``reporting.macro_context.enabled`` is true AND the configured
    notes file exists — never generated content. Returns the note lines, or None.
    """
    mc = ((cfg or {}).get("reporting", {}) or {}).get("macro_context") or {}
    if not mc.get("enabled"):
        return None
    from pathlib import Path
    notes = Path(mc.get("notes_file", ""))
    if not notes.is_file():
        return None
    lines = [ln.strip() for ln in notes.read_text(encoding="utf-8").splitlines()]
    return [ln for ln in lines if ln and not ln.startswith("#")] or None
