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
    "youtube": "YouTube",
    "display": "Display",
    "ctv": "CTV",
    "audio": "Audio",
    "jobboards": "Job boards",
    "email": "Email",
    "organic_search": "Organic search",
    "social_organic": "Organic social",
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


def _partial_edge_weeks(series: pd.Series, frac: float = 0.65) -> pd.Index:
    """Index labels of the flight's clipped opening/closing week(s).

    A campaign that starts/ends mid-week leaves the FIRST and/or LAST weekly bucket
    covering only a few days, so its spend/volume is a fraction of a full week — and a
    trailing-window delta that includes it reads as a collapse that never happened (the
    live "-12% MoM" that is really ~-3%). The pipeline calendar-gap-fills interior weeks
    to full buckets, so only an EDGE bucket can be a genuine part-week; we flag one whose
    value is < ``frac`` of the interior median. (Detecting via a coverage column would be
    cleaner, but that column would change the hash the stamped AI artifacts are keyed on.)

    Detect on a VOLUME/spend series and reuse the result for derived ratios (a cost-per
    ratio stays ~normal in a part-week, so it can't self-detect).
    """
    s = series.dropna()
    if len(s) < 4:
        return s.index[:0]
    interior = s.iloc[1:-1]
    med = float(interior.median()) if len(interior) else float("nan")
    if not med or pd.isna(med) or med <= 0:
        return s.index[:0]
    drop = [lbl for lbl, first_last in ((s.index[0], s.iloc[0]), (s.index[-1], s.iloc[-1]))
            if float(first_last) < frac * med]
    return pd.Index(drop)


def _recent_vs_prior(series: pd.Series, window: int = 4,
                     exclude: pd.Index | None = None) -> float:
    """Pct change of the trailing ``window`` weeks vs the ``window`` before them.

    ``exclude`` drops known part-weeks (from ``_partial_edge_weeks``, computed once on a
    volume series) so the same weeks are removed from every derived series before the
    window is taken. Without it, a clipped edge week silently skews the delta.
    """
    s = series.dropna()
    if exclude is not None and len(exclude):
        s = s.drop([e for e in exclude if e in s.index])
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

    spend_w = paid.groupby("date")["spend"].sum().sort_index()
    out_w = paid.groupby("date")[col].sum(min_count=1).sort_index()
    total_out = float(out_w.sum())
    # detect the flight's part-weeks once (on spend) and exclude them from every delta
    _partial = _partial_edge_weeks(spend_w)

    def _delta(series: pd.Series) -> str | None:
        pct = _recent_vs_prior(series, exclude=_partial)
        if pd.isna(pct):
            return None
        return ("flat vs prior 4 wks" if abs(pct) < 0.005
                else f"{pct * 100:+.0f}% vs prior 4 wks")

    tiles = [
        {"label": "Spend", "value": _money(spend_w.sum()), "delta": _delta(spend_w),
         "delta_color": "off", "help": "Paid media spend over the reporting period."},
        {"label": out_label.capitalize(), "value": f"{total_out:,.0f}",
         "delta": _delta(out_w), "delta_color": "normal",
         "help": ("Analytics-measured outcomes on paid campaigns." if measured
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
        sess_n = float(sess_w.sum())
        # compact form for the big supporting count ("6.57M", matching $37.50M) — the
        # hero outcome keeps full precision, context tiles don't need 7 digits
        sess_str = f"{sess_n / 1e6:.2f}M" if sess_n >= 1e6 else f"{sess_n:,.0f}"
        tiles.append({
            "label": "Sessions", "value": sess_str,
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


def _outcome_relabel(key: str, label: str, kpi_label: str, measured: bool,
                     blended: bool = False) -> str:
    """Relabel the outcome-tier metrics with the engagement's own KPI wording, so the
    scorecard says "Application starts" / "Cost / application start" — consistent with the
    tiles and prose, and free of the hardcoded, wrong-here "(GA4)" vendor tag.

    ``blended``: the engagement has NON-PAID outcome rows, so the national metric mixes
    organic outcomes under paid spend — a different number from the tile row's
    paid-only cost. Say so in the label, or the two read as a contradiction ($504 paid
    vs $232 blended, live finding 2026-07-13)."""
    if not measured:
        return label
    if key == "key_events":
        return (f"{kpi_label.capitalize()} (all traffic)" if blended
                else kpi_label.capitalize())
    if key == "cost_per_key_event":
        return (f"Cost / {_singular(kpi_label)} (blended, all traffic)" if blended
                else f"Cost / {_singular(kpi_label)}")
    return label


def tier_scorecard(weekly: pd.DataFrame, tier: str, targets: dict | None = None,
                   kpi_label: str = "key events",
                   config_target_keys: set | None = None) -> dict:
    """Goal-gauge scorecard for one pyramid tier (the Awareness/Engagement/Action band).

    Returns ``{tier, label, pace[], rag[], grid[], measured, relative_bands}``:
    ``pace`` = pacing bullets (volume metrics with a configured goal), ``rag`` = graded
    efficiency/quality bullets, ``grid`` = the totals card (Spend + un-goaled volumes).

    Each graded ``rag`` entry carries a ``provenance`` string ("client target" /
    "industry benchmark" / "channel spread") so prose can't call a benchmark band the
    "client's own configured target". ``config_target_keys`` = metric keys whose bands
    came from explicit config (vs the report-spec agent's benchmark suggestion).
    """
    targets = targets or {}
    config_target_keys = config_target_keys or set()
    reg = M.load_metric_registry()
    nat = {r["metric"]: r for r in M.compute_metrics(weekly, by=None, registry=reg)
           .to_dict("records")}
    paid = weekly[weekly["channel"].isin(_paid_channels(weekly))]
    per = (M.compute_metrics(paid, by="channel", registry=reg)
           if not paid.empty else None)

    rag_keys = list(_TIER_RAG.get(tier, []))
    vol_keys = list(_TIER_VOLUME.get(tier, []))
    measured = _has_measured(weekly)
    if tier == "outcome" and not measured:     # no measured series → fall back to claimed/CPA
        rag_keys = ["cpa" if k == "cost_per_key_event" else k for k in rag_keys]
        vol_keys = ["conversions" if k == "key_events" else k for k in vol_keys]
    # non-paid rows contribute outcomes → the national ratio blends organic outcomes
    # under paid spend; labels must say so (vs the tile row's paid-only cost)
    nonpaid = weekly[~weekly["channel"].isin(_paid_channels(weekly))]
    blended = bool(measured and "key_events" in nonpaid.columns
                   and float(nonpaid["key_events"].fillna(0).sum()) > 0)

    def _prov(key: str, mode: str) -> str:
        if mode != "absolute":
            return "channel spread"
        return "client target" if key in config_target_keys else "industry benchmark"

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
        rag.append({"key": key,
                    "label": _outcome_relabel(key, rec["label"], kpi_label, measured,
                                              blended=blended),
                    "value_str": _fmt(rec["value"], rec["format"]),
                    "provenance": _prov(key, g["mode"]), **g})

    pace, grid = [], []
    grid.append(("Spend", _money(float(paid["spend"].sum()))))
    for key in vol_keys:
        rec = nat.get(key)
        if rec is None or pd.isna(rec["value"]) or float(rec["value"]) == 0.0:
            continue                           # unpopulated column → omit, don't show "0"
        val, vstr = float(rec["value"]), _fmt(rec["value"], rec["format"])
        rec = {**rec, "label": _outcome_relabel(key, rec["label"], kpi_label, measured,
                                                blended=blended)}
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
    partial = _partial_edge_weeks(total)
    pct = _recent_vs_prior(total, exclude=partial)
    # peak among COMPLETE weeks — a clipped edge week can't be the "peak"
    complete = total.drop([e for e in partial if e in total.index])
    peak_date = complete.idxmax() if len(complete) else total.idxmax()
    peak_val = float(complete.max()) if len(complete) else float(total.max())

    # top paid channel by the same outcome column
    per_paid = (weekly[weekly["channel"].isin(paid)]
                .groupby("channel")[col].sum(min_count=1).dropna().sort_values())
    top_channel, top_n = (per_paid.index[-1], float(per_paid.iloc[-1])) \
        if len(per_paid) else (None, 0.0)

    mixed = paid_share == paid_share and paid_share < 0.995   # not an all-paid flight
    title = f"{label.capitalize()} are {_trend_phrase(pct)} month-on-month"
    narrative = (
        f"The period produced **{total_n:,.0f} {label}**"
        + (f", **{paid_share * 100:.0f}%** of them on paid campaigns" if mixed else "")
        + f"; the last four weeks are {_trend_phrase(pct)} versus the four before. ")
    if top_channel:
        narrative += (f"**{channel_label(top_channel)}** is the largest paid "
                      f"contributor ({top_n:,.0f} {label}).")
    if len(partial):
        narrative += (" _The trend excludes the flight's part-week(s) at the start/finish "
                      "(only a few days each), which would otherwise read as a false dip._")
    if not measured:
        narrative += (" _No analytics-measured outcome series yet — these are the "
                      "platforms' own conversion claims; treat them as directional._")
    return {
        "title": title, "narrative": narrative, "series": series,
        "measured": measured, "label": label, "trend_pct": pct,
        "partial_weeks": [(d, float(total.loc[d])) for d in partial if d in total.index],
        "annotations": [(peak_date, peak_val, f"peak week: {peak_val:,.0f}")],
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

    # blended cost = total spend / total outcomes — matches the headline tile exactly
    # (an unweighted mean of the per-channel costs would disagree by a few cents)
    blended = float(per["spend"].sum()) / float(per[col].sum())
    cheap, dear = per.iloc[0], per.iloc[-1]
    if len(per) >= 2 and dear["cost_per"] / cheap["cost_per"] >= 1.15:
        mult = dear["cost_per"] / cheap["cost_per"]
        title = (f"{channel_label(cheap['channel'])} delivers {_a(label)} for "
                 f"{_money(cheap['cost_per'])} — {mult:.1f}x cheaper than "
                 f"{channel_label(dear['channel'])}")
    else:
        title = (f"Paid channels deliver {_a(label)} for about {_money(blended)}")
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
    total_spend = float(cum.iloc[-1])       # cumulative TOTAL keeps every dollar spent
    n_weeks = len(by_week)
    # run rate = a typical FULL week, so exclude the flight's clipped edge weeks (else a
    # part-week drags the average down and the projection understates)
    full = by_week.drop([e for e in _partial_edge_weeks(by_week) if e in by_week.index])
    run_rate = float((full if len(full) else by_week).iloc[-4:].mean())

    out = {"cumulative": cum, "run_rate": run_rate, "total_spend": total_spend,
           "n_weeks": n_weeks, "budget": None}
    total_budget = float(budget.get("total", 0) or 0) if budget else 0.0
    flight_weeks = int(budget.get("flight_weeks", 0) or 0) if budget else 0
    if total_budget > 0 and flight_weeks > 0:
        pct_spent = total_spend / total_budget
        pct_elapsed = min(n_weeks / flight_weeks, 1.0)
        gap = pct_spent - pct_elapsed
        complete = n_weeks >= flight_weeks
        if abs(gap) < 0.05:
            phrase, verdict = "on plan", "on_plan"
        else:
            phrase = (f"{abs(gap) * 100:.0f} points {'ahead of' if gap > 0 else 'behind'} "
                      "plan")
            verdict = "ahead" if gap > 0 else "behind"
        if complete:
            # a finished flight gets a CLOSE-OUT read — projecting a run rate over a
            # flight that already ended manufactures a phantom over/under-spend
            title = f"The flight closed {phrase}"
            narrative = (
                f"The {flight_weeks}-week flight is complete: spend closed at "
                f"**{_money(total_spend)}** against the **{_money(total_budget)}** plan "
                f"(**{pct_spent * 100:.0f}%**). The final four weeks ran at "
                f"{_money(run_rate)}/week.")
        else:
            title = f"Spend is pacing {phrase}"
            narrative = (
                f"**{_money(total_spend)}** of the **{_money(total_budget)}** budget is spent "
                f"(**{pct_spent * 100:.0f}%**) with **{pct_elapsed * 100:.0f}%** of the "
                f"{flight_weeks}-week flight elapsed. The current run rate is "
                f"{_money(run_rate)}/week; at that pace the budget lands at "
                f"{_money(run_rate * flight_weeks)} over the full flight.")
        out.update({"budget": {"total": total_budget, "flight_weeks": flight_weeks,
                               "pct_spent": pct_spent, "pct_elapsed": pct_elapsed,
                               "verdict": verdict, "complete": complete}})
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
        _spend_w = paid.groupby("date")["spend"].sum().sort_index()
        pct = _recent_vs_prior(paid.groupby("date")[col].sum(min_count=1).sort_index(),
                               exclude=_partial_edge_weeks(_spend_w))

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


# ---------------------------------------------------------------- recruiting pipeline
# The post-submission applicant gates, in portal-tracker order. REPORTING layer only:
# the stages are selection-driven and lag media by months — never a modeling target.
PIPELINE_STAGE_ORDER = ["initial_screening", "meet_greet", "testing",
                        "conditional_offer", "background_investigation", "final_offer"]
PIPELINE_STAGE_LABELS = {
    "initial_screening": "Initial screening",
    "meet_greet": "Meet & greet",
    "testing": "Testing",
    "conditional_offer": "Conditional offer",
    "background_investigation": "Background investigation",
    "final_offer": "Final offer",
}

PIPELINE_CENSOR_NOTE = ("Pipeline still maturing: final offers completing now stem "
                        "from applications submitted ~9–12 months ago, so recent "
                        "cohorts under-count at the later gates.")


def recruiting_pipeline_insight(stages: pd.DataFrame | None) -> dict | None:
    """The 6-stage post-submission applicant funnel (CRM/ATS calendar-week counts).

    ``stages`` is the frame from ``utils.load_pipeline_stages`` (date, stage, count,
    optionally geo/initiative/channel). Returns None when absent/empty — the block
    simply doesn't render on engagements without an applicant pipeline.

    Honesty rules baked in: counts are calendar-week totals (not one cohort), the
    right-censoring is stated, and the narrative draws the MMM boundary — media buys
    applications; it cannot pass a polygraph.
    """
    if stages is None or len(stages) == 0 or "stage" not in stages.columns:
        return None
    per = stages.groupby("stage")["count"].sum()
    order = [s for s in PIPELINE_STAGE_ORDER if s in per.index and per[s] > 0]
    if len(order) < 2:
        return None
    rows = []
    prev_stage, prev_val = None, None
    for s in order:
        v = float(per[s])
        rate = (v / prev_val) if prev_val else float("nan")
        rows.append({"stage": s, "label": PIPELINE_STAGE_LABELS.get(s, s),
                     "value": v, "from": prev_stage, "step_rate": rate})
        prev_stage, prev_val = s, v
    df = pd.DataFrame(rows)

    first, last = df.iloc[0], df.iloc[-1]
    overall = last["value"] / first["value"] if first["value"] else float("nan")
    steps = df.dropna(subset=["step_rate"])
    hardest = steps.loc[steps["step_rate"].idxmin()] if len(steps) else None

    title = (f"Selection does the filtering: {overall * 100:.0f}% of screened "
             f"applicants have cleared every gate to a final offer")
    co = df.set_index("stage")["value"].get("conditional_offer")
    co_bit = f"**{co:,.0f}** conditional offers and " if co is not None else ""
    narrative = (
        f"Beyond the media funnel, the CRM has recorded **{first['value']:,.0f}** "
        f"applicants entering {first['label'].lower()}, {co_bit}"
        f"**{last['value']:,.0f}** final offers to date. ")
    if hardest is not None:
        narrative += (
            f"The steepest gate is **{hardest['label']}**, which only "
            f"**{hardest['step_rate'] * 100:.0f}%** of "
            f"{PIPELINE_STAGE_LABELS.get(hardest['from'], str(hardest['from'])).lower()} "
            "candidates clear. ")
    narrative += (
        f"_{PIPELINE_CENSOR_NOTE} Counts are calendar-week CRM totals, so "
        "stage-to-stage rates compare everyone clearing each gate in the window, not a "
        "single cohort._ **Media buys applications; it cannot pass a polygraph** — these "
        "gates are selection outcomes, reported for context and never attributed to "
        "media.")
    return {"title": title, "narrative": narrative, "stages": df,
            "overall_rate": overall, "censor_note": PIPELINE_CENSOR_NOTE}


def applicant_quality_insight(stages: pd.DataFrame | None,
                              min_screened: int = 500) -> dict | None:
    """Applicant QUALITY by last-touch channel: what share of each channel's screened
    applicants clear the FIRST gate (initial screening → meet & greet).

    First gate only, deliberately: it has the shortest lag from submission, so it is
    the least right-censored stage — later gates under-count recent cohorts unevenly
    and would misread as channel differences. Channels below ``min_screened`` screened
    applicants are excluded (a 5% rate on 155 people is noise, not signal).

    HONESTY: the channel column is the CRM's LAST-TOUCH attribution — descriptive,
    never causal, and never a media-performance verdict. Quality of who applies is a
    selection observation, not something to grade media on.
    """
    if stages is None or len(stages) == 0 or "channel" not in stages.columns:
        return None
    s = stages[stages["stage"].isin(["initial_screening", "meet_greet"])]
    if s.empty:
        return None
    pv = (s.groupby(["channel", "stage"])["count"].sum().unstack("stage"))
    if not {"initial_screening", "meet_greet"}.issubset(pv.columns):
        return None
    pv = pv[pv["initial_screening"] >= float(min_screened)].copy()
    if len(pv) < 2:
        return None
    pv["survival"] = pv["meet_greet"] / pv["initial_screening"]
    pv = (pv.reset_index().rename(columns={"initial_screening": "screened",
                                           "meet_greet": "advanced"})
            .sort_values("survival", ascending=False).reset_index(drop=True))
    best, worst = pv.iloc[0], pv.iloc[-1]
    title = (f"{channel_label(best['channel'])} applicants clear initial screening at "
             f"{best['survival'] * 100:.0f}% — {channel_label(worst['channel'])} at "
             f"{worst['survival'] * 100:.0f}%")
    narrative = (
        "Share of each channel's screened applicants who clear the first gate "
        "(initial screening → meet & greet): "
        + ", ".join(f"**{channel_label(r['channel'])}** {r['survival'] * 100:.0f}% "
                    f"({r['screened']:,.0f} screened)" for _, r in pv.iterrows())
        + ". _Channel is the CRM's **last-touch** attribution — descriptive applicant "
        "quality, not causal media credit; channels with fewer than "
        f"{min_screened:,} screened applicants are excluded. Only the first gate is "
        "compared: later gates lag submission by months, so recent cohorts would "
        "under-count unevenly._")
    return {"title": title, "narrative": narrative, "per_channel": pv,
            "min_screened": min_screened}


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
