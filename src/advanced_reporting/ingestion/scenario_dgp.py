"""Scenario-driven DGP for the FBI recruiting engagement (FBI_CAMPAIGN_DATA_BRIEF.md P1).

Reads a validated scenario spec (``ingestion/scenario.py``) and produces a known
ground-truth synthetic engagement:

* ``media_weekly`` — one row per week x channel x geo x initiative x sub-entity, with
  spend + delivery (impressions/clicks), mid-funnel (sessions/engaged/starts), the
  platform-CLAIMED conversions and the GA4-verifiable key events, plus the decoded-name
  fields (objective/audience/creative) so the emitters can write grammar-conformant names.
* ``kpi_weekly`` — the MMM target: week x geo SUBMITTED applications + the control cols.
* ``pipeline_stages`` — the post-submission applicant funnel: calendar-week counts by
  week x geo x initiative x stage (+ a last-touch channel), right-censored near the edge.
* ``ground_truth`` — per-channel true incremental contribution + ROI + baseline share,
  obeying the ACCOUNTING IDENTITY exactly: baseline + Sigma channel contributions = KPI.

Design (why it is a *test*, not just data):
- The MMM target is CONSTRUCTED as ``submitted = baseline + Sigma contribution_c`` where
  ``contribution_c = beta_c * hill(adstock(spend_c))``. The identity is therefore exact by
  construction (no noise on the total), and each channel's true incremental effect is
  exactly ``beta_c * hill(adstock(.))`` — what the MMM must recover.
- betas are scaled so paid media drives the configured share of submitted apps and the
  per-channel average ROI RANK matches ``roi_apps_per_1k``; the Hill ``shape``/``half_sat``
  then set each channel's MARGINAL curve (google_search deep in the plateau = stress d).
- Delivery (impressions/clicks/sessions/starts) is generated separately from spend via the
  CPM/CPC/CTR + funnel rates and scaled to the calibration bands; GA4 key events verify
  starts EXCEPT for the zero-click channel (ctv), whose UTM-less traffic GA4 barely sees
  while the platform claims thousands (stress g).

Deterministic (seeded). ``--mini`` (16 weeks x 3 geos) keeps CI fast; the full run is manual.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..mmm.transforms import geometric_adstock, hill_saturation

# Objective + a small sub-entity plan per channel. Each entry becomes an ad_group /
# creative (ad-level channels) or is folded into the campaign name (campaign-grain
# channels). audience_type/detail draw from the scenario naming vocab; creatives too.
# `share` splits the channel's spend across its sub-entities.
_CHANNEL_PLAN = {
    "google_search": ("CONVERT", [
        {"audience_type": "PROSPECT", "audience_detail": "INT-LAW", "creative": "", "fmt": "", "share": 0.6},
        {"audience_type": "RETARGET", "audience_detail": "SITE-90D", "creative": "", "fmt": "", "share": 0.4},
    ]),
    "youtube": ("AWARENESS", [
        {"audience_type": "PROSPECT", "audience_detail": "STEM-GRAD", "creative": "HERO", "fmt": "VID", "share": 1.0},
    ]),
    "meta": ("CONVERT", [
        {"audience_type": "PROSPECT", "audience_detail": "LAL-1PCT", "creative": "DAYINLIFE", "fmt": "VID", "share": 0.55},
        {"audience_type": "RETARGET", "audience_detail": "ENGAGE-30D", "creative": "MISSION-STAT", "fmt": "STATIC", "share": 0.45},
    ]),
    "linkedin": ("CONSIDER", [
        {"audience_type": "PROSPECT", "audience_detail": "VET-MIL", "creative": "TESTIM-AGENT", "fmt": "VID", "share": 0.5},
        {"audience_type": "PROSPECT", "audience_detail": "CAMPUS", "creative": "MISSION-STAT", "fmt": "STATIC", "share": 0.5},
    ]),
    "ctv": ("AWARENESS", [
        {"audience_type": "PROSPECT", "audience_detail": "BROAD", "creative": "HERO", "fmt": "VID", "share": 1.0},
    ]),
    "display": ("AWARENESS", [
        {"audience_type": "PROSPECT", "audience_detail": "INT-LAW", "creative": "MISSION-STAT", "fmt": "STATIC", "share": 1.0},
    ]),
    "jobboards": ("CONVERT", [
        {"audience_type": "RETARGET", "audience_detail": "ABANDON-APP", "creative": "", "fmt": "", "share": 1.0},
    ]),
    "audio": ("AWARENESS", [
        {"audience_type": "PROSPECT", "audience_detail": "BROAD", "creative": "", "fmt": "", "share": 1.0},
    ]),
}

_NONPAID_CHANNELS = ("organic_search", "direct", "email", "social_organic")
_MINI_WEEKS = 16
_MINI_GEOS = 3
# Single-token channel codes for the campaign-name grammar. The canonical channel keys
# (google_search, social_organic) contain underscores, which are the grammar's delimiter —
# so the NAME uses a clean one-token code that decode_campaign_name reads as segment 1.
_CHANNEL_TOKEN = {
    "google_search": "GSEARCH", "youtube": "YOUTUBE", "meta": "META", "linkedin": "LINKEDIN",
    "ctv": "CTV", "display": "DISPLAY", "jobboards": "JOBBOARDS", "audio": "AUDIO",
}
# Per-channel flighting-rhythm PHASE (fraction of a ~quarterly cycle). Distinct phases give
# each channel its own on/off rhythm so its spend is identifiable — not collinear with the
# smooth baseline trend nor with every other channel. meta + youtube deliberately SHARE a
# phase (stress (a): the collinear pair the MMM must hedge on with wide intervals).
_CHANNEL_PHASE = {
    "google_search": 0.0, "meta": 0.30, "youtube": 0.30, "linkedin": 0.55,
    "ctv": 0.70, "display": 0.15, "jobboards": 0.45, "audio": 0.85,
}
# View/listen-through "starts" basis per impression for zero-click channels (ctv/audio) —
# they drive no clicks, so their platform-CLAIMED conversions (and the tiny GA4-verifiable
# slice) come from impressions instead. Tuned so ctv claims thousands (stress g).
_ZC_VT_RATE = 1.2e-5


@dataclass
class ScenarioData:
    media_weekly: pd.DataFrame
    kpi_weekly: pd.DataFrame
    pipeline_stages: pd.DataFrame
    ground_truth: dict
    weeks: pd.DatetimeIndex
    geos: list
    spec: dict = field(default_factory=dict)


# ----------------------------------------------------------------- helpers
def _weeks(spec: dict, mini: bool, n_weeks: int | None = None) -> pd.DatetimeIndex:
    start = pd.Timestamp(spec["flight"]["start"])
    n = n_weeks if n_weeks is not None else (_MINI_WEEKS if mini else int(spec["flight"]["weeks"]))
    return pd.date_range(start, periods=n, freq="7D")


def _geos(spec: dict, mini: bool, n_geos: int | None = None) -> list[dict]:
    geos = spec["geos"]
    k = n_geos if n_geos is not None else (_MINI_GEOS if mini else len(geos))
    return geos[:k]


def _season(weeks: pd.DatetimeIndex, spec: dict) -> np.ndarray:
    """Multiplicative seasonal lift per week (grad season + new-year spike)."""
    s = np.ones(len(weeks))
    seas = spec.get("seasonality", {})
    for key in ("grad_season", "new_year"):
        cfg = seas.get(key)
        if not cfg:
            continue
        months, lift = set(cfg["months"]), float(cfg["lift"])
        s *= np.where(weeks.month.isin(list(months)), lift, 1.0)
    return s


def _controls(weeks: pd.DatetimeIndex, spec: dict, rng) -> pd.DataFrame:
    """Emit the MMM control columns: a slow-moving index + binary news shocks."""
    n = len(weeks)
    ctl = spec.get("controls", {})
    t = np.arange(n)
    ui = ctl.get("unemployment_index", {})
    base, amp = float(ui.get("base", 4.0)), float(ui.get("amplitude", 1.0))
    unemployment = base + amp * np.sin(2 * np.pi * (t + 6) / 104.0) + rng.normal(0, 0.05, n)
    ns = ctl.get("news_spike_flag", {})
    flag = np.zeros(n, dtype=int)
    if n > 4:
        starts = rng.choice(np.arange(2, n - 2), size=min(int(ns.get("n_shocks", 4)), n // 8 + 1),
                            replace=False)
        for st in starts:
            flag[st: st + int(rng.integers(1, 3))] = 1
    return pd.DataFrame({"date": weeks, "unemployment_index": unemployment.round(3),
                         "news_spike_flag": flag})


def _flight_weight(channel: str, spec: dict, weeks: pd.DatetimeIndex, geo_codes: list[str],
                   rng) -> np.ndarray:
    """A (weeks x geos) non-negative spend-weight grid encoding the stress cases:
    seasonality, National-Recruiting-Week bursts, LinkedIn dark weeks, ctv geo launch."""
    n_w, n_g = len(weeks), len(geo_codes)
    w = np.ones((n_w, n_g))
    # DAMPENED shared seasonality on media: recruiters do lift spend in grad/new-year season,
    # but applying the full lift to every channel makes them all collinear (and collinear
    # with the baseline season), which no MMM can untangle. Keep ~40% of the lift shared and
    # let the DISTINCT per-channel rhythms + bursts + dark/launch stress carry identifiability.
    w *= (1.0 + 0.4 * (_season(weeks, spec) - 1.0))[:, None]
    stress = spec.get("stress", {})

    # (c) burst: ~2.5x on search/meta/youtube for 2 weeks (National Recruiting Week)
    burst = stress.get("burst", {})
    if channel in burst.get("channels", []):
        for p in burst.get("periods", []):
            pstart = pd.Timestamp(p["start"])
            mask = np.asarray((weeks >= pstart) & (weeks < pstart + pd.Timedelta(weeks=int(p["weeks"]))))
            w[mask, :] *= float(burst.get("multiplier", 2.5))

    # (b) dark channel: LinkedIn paused for a stretch (budget freeze) -> identifiability
    dark = stress.get("dark_channel", {})
    if channel == dark.get("channel"):
        d0 = pd.Timestamp(dark["dark_weeks"]["start"])
        mask = np.asarray((weeks >= d0) & (weeks < d0 + pd.Timedelta(weeks=int(dark["dark_weeks"]["weeks"]))))
        w[mask, :] = 0.0

    # (f) geo lever: ctv launches in a few regions first, all regions later
    lever = stress.get("geo_lever", {})
    if channel == lever.get("channel"):
        first = set(lever["launch_first"]["geos"])
        d_first = pd.Timestamp(lever["launch_first"]["date"])
        d_all = pd.Timestamp(lever["launch_all"])
        for gi, code in enumerate(geo_codes):
            live = d_first if code in first else d_all
            w[np.asarray(weeks < live), gi] = 0.0

    # channel-specific ~quarterly flighting rhythm (distinct phase per channel) — the
    # structural spend variation that makes each channel identifiable to the MMM.
    t = np.arange(n_w)
    phase = _CHANNEL_PHASE.get(channel, 0.0)
    rhythm = 1.0 + 0.35 * np.sin(2 * np.pi * (t / 13.0 + phase))
    w *= rhythm[:, None]

    # Holiday QUIET weeks: recruiting media goes near-dark late December every year. When
    # all paid dips together, weekly submissions fall back toward BASELINE — the clean
    # low-spend observations an MMM needs to identify baseline demand (without them,
    # always-on media absorbs the baseline level and paid ROI is over-credited).
    quiet = np.asarray((weeks.month == 12) & (weeks.day >= 18))
    w[quiet, :] *= 0.12

    w *= rng.lognormal(0.0, 0.18, (n_w, n_g))            # week-to-week wobble
    return np.clip(w, 0.0, None)


def _geo_multipliers(geos: list[dict]) -> np.ndarray:
    return np.array([float(g["population"]) * float(g["base_multiplier"]) for g in geos])


# ----------------------------------------------------------------- core build
def generate(spec: dict, *, mini: bool = False, seed: int | None = None,
             n_weeks: int | None = None, n_geos: int | None = None) -> ScenarioData:
    """Run the DGP for a validated scenario spec. Returns a ``ScenarioData`` bundle.

    ``mini`` = the 16-week x 3-geo fast preset; ``n_weeks``/``n_geos`` override the window
    explicitly (e.g. tests that need enough weeks for pipeline cohorts to mature)."""
    rng = np.random.default_rng(seed if seed is not None else int(spec.get("seed", 0)))
    weeks = _weeks(spec, mini, n_weeks)
    geos = _geos(spec, mini, n_geos)
    geo_codes = [g["code"] for g in geos]
    geo_mult = _geo_multipliers(geos)
    geo_mult = geo_mult / geo_mult.sum()
    n_w, n_g = len(weeks), len(geos)
    years = n_w / 52.0

    initiatives = spec["initiatives"]
    init_codes = [i["code"] for i in initiatives]
    init_share = {i["code"]: float(i["spend_share"]) for i in initiatives}
    channels = spec["channels"]
    total_spend = float(spec["budget"]["total_usd"]) * (years / (int(spec["flight"]["weeks"]) / 52.0))

    # 1) SPEND grid per paid channel (weeks x geos), plus per-(channel,geo) contribution.
    paid = [c for c, v in channels.items() if v["kind"] == "paid"]
    spend_grid: dict[str, np.ndarray] = {}
    contrib_sat: dict[str, np.ndarray] = {}       # hill(adstock(spend)) per (w,g)
    for ch in paid:
        cfg = channels[ch]
        share = float(cfg["spend_share"])
        w = _flight_weight(ch, spec, weeks, geo_codes, rng)
        w *= geo_mult[None, :]
        # A channel whose whole flight weight is zero in this window (e.g. ctv before its
        # geo-lever launch, or a short --mini window that predates the launch) is simply
        # INACTIVE here -> zero spend. (Never fall back to uniform: that would fabricate
        # spend for an unlaunched channel.)
        grid = np.zeros((n_w, n_g)) if w.sum() == 0 else w / w.sum() * (total_spend * share)
        spend_grid[ch] = grid
        # ground-truth response: adstock per geo column, then Hill saturation
        decay = float(cfg["adstock_decay"])
        hill = cfg.get("hill", {})
        half, shape = float(hill.get("half_sat_spend", grid.mean() or 1.0)), float(hill.get("shape", 1.0))
        ad = np.column_stack([geometric_adstock(grid[:, gi], decay) for gi in range(n_g)])
        contrib_sat[ch] = hill_saturation(ad, half_sat=half, slope=shape)

    # 2) CONTRIBUTIONS: scale betas so paid drives the target share, ROI rank = roi_apps_per_1k.
    submitted_total = float(spec["funnel"]["submitted_per_year"]) * years
    paid_share = float(np.mean(spec["funnel"]["paid_share_of_submitted"]))
    paid_total = paid_share * submitted_total
    weight = {ch: float(channels[ch]["roi_apps_per_1k"]) * spend_grid[ch].sum() for ch in paid}
    wsum = sum(weight.values())
    contribution: dict[str, np.ndarray] = {}
    beta: dict[str, float] = {}
    for ch in paid:
        target_c = paid_total * weight[ch] / wsum
        sat_sum = contrib_sat[ch].sum() or 1.0
        beta[ch] = target_c / sat_sum
        contribution[ch] = beta[ch] * contrib_sat[ch]

    # 3) BASELINE: organic demand. Controls are computed FIRST and genuinely DRIVE baseline
    #    (unemployment_index raises demand ~+corr; news shocks add short lifts) so the MMM's
    #    control columns are informative — the model explains slow baseline swings with the
    #    controls instead of misattributing them to steady-spend channels.
    baseline_total = submitted_total - paid_total
    kpi = _controls(weeks, spec, rng)
    ui = kpi["unemployment_index"].to_numpy()
    corr = float(spec.get("controls", {}).get("unemployment_index", {}).get("correlation", 0.35))
    ui_mult = 1.0 + corr * (ui - ui.mean()) / (ui.std() or 1.0)      # demand rises with UI
    news_lift = float(spec.get("controls", {}).get("news_spike_flag", {}).get("lift", 1.15))
    news_mult = 1.0 + (news_lift - 1.0) * kpi["news_spike_flag"].to_numpy()
    # Baseline seasonality is a SINGLE annual harmonic (peaks around grad season), which the
    # MMM's const+trend+annual-sin/cos basis + these controls can represent exactly — so the
    # model captures baseline cleanly and doesn't over-credit paid. (Media flighting keeps
    # the richer bimodal grad + new-year seasonality via _season; that's a MEDIA driver.)
    t = np.arange(n_w)
    base_seas = 1.0 + 0.08 * np.sin(2 * np.pi * (t - 18) / 52.0)     # mild annual (flat-ish)
    demand = (base_seas * ui_mult * news_mult)[:, None] * geo_mult[None, :]
    base_shape = demand * rng.lognormal(0.0, 0.06, (n_w, n_g))
    baseline = base_shape / base_shape.sum() * baseline_total

    # 4) SUBMITTED (the MMM target) — exact accounting identity, per (week, geo).
    submitted = baseline.copy()
    for ch in paid:
        submitted += contribution[ch]

    kpi_rows = []
    for gi, code in enumerate(geo_codes):
        kpi_rows.append(pd.DataFrame({
            "date": weeks, "geo": code,
            "submitted_applications": submitted[:, gi],
            "unemployment_index": kpi["unemployment_index"].to_numpy(),
            "news_spike_flag": kpi["news_spike_flag"].to_numpy(),
        }))
    kpi_weekly = pd.concat(kpi_rows, ignore_index=True)

    # 5) DELIVERY + mid-funnel per channel x geo x initiative x sub-entity.
    media = _build_media(spec, weeks, geos, geo_codes, init_codes, init_share,
                         spend_grid, channels, rng, years)

    # 6) GROUND TRUTH (per-channel contribution + ROI + baseline share; identity check).
    ground_truth = _ground_truth(spec, paid, spend_grid, contribution, beta, channels,
                                 baseline, submitted, weeks, geos)

    # 7) APPLICANT PIPELINE cohorts (post-submission reporting layer).
    pipeline_stages = _pipeline(spec, weeks, geo_codes, init_codes, submitted, spend_grid,
                                contribution, geo_mult, init_share, paid)

    return ScenarioData(media_weekly=media, kpi_weekly=kpi_weekly,
                        pipeline_stages=pipeline_stages, ground_truth=ground_truth,
                        weeks=weeks, geos=geos, spec=spec)


def _build_media(spec, weeks, geos, geo_codes, init_codes, init_share, spend_grid,
                 channels, rng, years) -> pd.DataFrame:
    """Explode each paid channel's (weeks x geos) spend across initiatives + sub-entities,
    deriving delivery + mid-funnel. Also emits non-paid GA4-only rows (baseline demand)."""
    market = spec["naming_vocab"]["markets"][0]
    funnel = spec["funnel"]
    c2s = float(funnel["click_to_session"]); s2e = float(funnel["session_to_engaged"])
    e2start = float(funnel["engaged_to_start"])
    starts_target = float(funnel["starts_per_year"]) * years
    n_w, n_g = len(weeks), len(geos)
    init_arr = np.array([init_share[c] for c in init_codes])
    init_arr = init_arr / init_arr.sum()
    tail = spec.get("unparsed_tail", {})
    tail_names = list(tail.get("names", []))
    tail_channels = set(tail.get("channels", []))
    tail_share = float(tail.get("spend_share", 0.0))

    rows = []
    for ch, grid in spend_grid.items():
        cfg = channels[ch]
        objective, plan = _CHANNEL_PLAN[ch]
        cpc = cfg.get("cpc"); cpm = cfg.get("cpm"); ctr = float(cfg.get("ctr") or 0.0)
        claim = cfg.get("claim_ratio")
        grain = cfg["grain"]
        aff = cfg.get("initiative_affinity", {})
        iw = np.array([init_share[c] * float(aff.get(c, 1.0)) for c in init_codes])
        iw = iw / iw.sum()
        for ii, icode in enumerate(init_codes):
            for sub in plan:
                # spend block for this channel x initiative x sub-entity (weeks x geos)
                block = grid * iw[ii] * float(sub["share"])
                if block.sum() == 0:
                    continue
                spend = block.flatten()
                # delivery
                if cpc:
                    clicks = spend / float(cpc) * rng.lognormal(0, 0.08, spend.size)
                    impressions = np.where(ctr > 0, clicks / max(ctr, 1e-6), spend / 3.0 * 1000)
                else:
                    impressions = spend / float(cpm) * 1000.0 * rng.lognormal(0, 0.08, spend.size)
                    clicks = impressions * ctr
                sessions = clicks * c2s
                engaged = sessions * s2e
                starts = engaged * e2start
                if ctr == 0.0:               # zero-click channel: view/listen-through basis
                    starts = impressions * _ZC_VT_RATE
                # GA4 verifiability: zero-click channels (ctv) barely register key events
                verify = 0.01 if ch == spec.get("stress", {}).get("zero_click", {}).get("channel") else 0.9
                ga4_key = starts * verify
                video = impressions * (0.2 if (cpm and objective == "AWARENESS") else 0.0)

                aud_t, aud_d = sub["audience_type"], sub["audience_detail"]
                creative, fmt = sub["creative"], sub["fmt"]
                campaign = f"{market}_{_CHANNEL_TOKEN[ch]}_{objective}_{aud_t}_{icode}"
                if grain in ("ad_group", "ad_set"):
                    ad_group = "_".join(x for x in (aud_t, aud_d) if x)
                elif grain == "creative":
                    ad_group = "_".join(x for x in (creative, fmt) if x)
                else:                                    # campaign grain
                    ad_group = ""
                # block is (n_w, n_g); its C-order flatten is week-major (week varies
                # slowest, geo fastest), so the date/geo labels must match: date repeats
                # each week across geos, geo tiles the geo list per week. (A tile/repeat
                # swap here silently scrambles every metric across week x geo cells.)
                rows.append(pd.DataFrame({
                    "date": np.repeat(weeks, n_g), "geo": np.tile(geo_codes, n_w),
                    "channel": ch, "initiative": icode, "objective": objective,
                    "campaign": campaign, "ad_group": ad_group,
                    "audience_type": aud_t if ad_group else "", "audience_detail": aud_d if ad_group else "",
                    "creative": creative if grain == "creative" else "",
                    "creative_format": fmt if grain == "creative" else "",
                    "grain": grain, "spend": spend,
                    "impressions": impressions, "clicks": clicks,
                    "sessions": sessions, "engaged_sessions": engaged,
                    "key_events": ga4_key, "starts_true": starts,
                    "conversions": 0.0, "video_views": video,   # conversions set post-scale
                }))
    media = pd.concat(rows, ignore_index=True)

    # Scale starts so TOTAL application starts (paid + organic) hit the calibration band:
    # ~starts_per_year/yr, of which paid drives `paid_share` (so blended cost-per-start and
    # start->submitted land in band). Paid and non-paid are scaled to their target shares.
    paid_share = float(np.mean(funnel["paid_share_of_submitted"]))
    paid_starts_target = paid_share * starts_target
    scale = paid_starts_target / max(media["starts_true"].sum(), 1.0)
    for col in ("sessions", "engaged_sessions", "key_events", "starts_true"):
        media[col] = media[col] * scale

    # Platform-CLAIMED conversions, computed post-scaling so the observed claim ratio
    # (conversions / GA4 key_events) matches the scenario exactly: conv = claim x key_events
    # for normal channels; the zero-click channel (ctv) claims against TRUE starts while GA4
    # verifies ~none (stress g -> extreme claim); audio has no claim (promo-code trickle).
    zero_click = spec.get("stress", {}).get("zero_click", {}).get("channel")
    claim_map = {ch: channels[ch].get("claim_ratio") for ch in channels}
    claim = media["channel"].map(claim_map).astype(float)
    conv = np.where(
        claim.isna(), 0.05 * media["starts_true"],                       # audio trickle
        np.where(media["channel"] == zero_click, claim.fillna(0) * media["starts_true"],
                 claim.fillna(0) * media["key_events"]))                 # normal channels
    media["conversions"] = conv

    # unparsed tail: rename ~tail_share of ad-level spend on tail channels to legacy names
    if tail_names and tail_share > 0:
        _apply_unparsed_tail(media, tail_channels, tail_share, tail_names, rng)

    media["conversions"] = media["conversions"].round(1)
    for c in ("impressions", "clicks", "sessions", "engaged_sessions", "key_events", "video_views"):
        media[c] = media[c].round().clip(lower=0)
    media["spend"] = media["spend"].round(2)
    media = media[media["spend"] > 0].reset_index(drop=True)

    # non-paid GA4-only rows (organic/direct/email/social) — baseline demand, no spend —
    # scaled to the remaining (1 - paid_share) of total starts.
    nonpaid = _nonpaid_rows(spec, weeks, geo_codes, rng, years)
    np_target = (1.0 - paid_share) * starts_target
    np_scale = np_target / max(nonpaid["starts_true"].sum(), 1.0)
    for col in ("sessions", "engaged_sessions", "key_events", "starts_true"):
        nonpaid[col] = (nonpaid[col] * np_scale).round()
    media = pd.concat([media, nonpaid], ignore_index=True)
    return media


def _apply_unparsed_tail(media, tail_channels, tail_share, tail_names, rng) -> None:
    """Rename a slice of ad-level rows to messy legacy ad_group names (in place) so
    ~``tail_share`` of TOTAL ad-level spend decodes to (unparsed) — concentrated on the
    tail channels (meta + search), the way real legacy names cluster."""
    ad_level = (media["ad_group"] != "") & (media["spend"] > 0)
    target = tail_share * float(media.loc[ad_level, "spend"].sum())
    pool = media.index[ad_level & media["channel"].isin(list(tail_channels))]
    if len(pool) == 0:
        return
    picked, acc = [], 0.0
    for i in rng.permutation(pool.to_numpy()):
        picked.append(i); acc += float(media.at[i, "spend"])
        if acc >= target:
            break
    names = rng.choice(tail_names, size=len(picked))
    for i, nm in zip(picked, names):
        media.at[i, "ad_group"] = str(nm)
        media.at[i, "audience_type"] = ""; media.at[i, "audience_detail"] = ""
        media.at[i, "creative"] = ""; media.at[i, "creative_format"] = ""


def _nonpaid_rows(spec, weeks, geo_codes, rng, years) -> pd.DataFrame:
    """Organic/direct/email/social rows: GA4 sessions + key events, zero spend."""
    n_w = len(weeks)
    seas = _season(weeks, spec)
    rows = []
    per_channel_starts = float(spec["funnel"]["starts_per_year"]) * years * 0.4 / (
        len(_NONPAID_CHANNELS) * len(geo_codes))
    for ch in _NONPAID_CHANNELS:
        base = per_channel_starts * (1.6 if ch in ("organic_search", "direct") else 0.5)
        for code in geo_codes:
            starts = base / n_w * seas * rng.lognormal(0, 0.12, n_w)
            sessions = starts / 0.02
            rows.append(pd.DataFrame({
                "date": weeks, "geo": code, "channel": ch, "initiative": "",
                "objective": "", "campaign": "(organic)", "ad_group": "",
                "audience_type": "", "audience_detail": "", "creative": "", "creative_format": "",
                "grain": "campaign", "spend": 0.0,
                "impressions": 0.0, "clicks": 0.0,
                "sessions": sessions.round(), "engaged_sessions": (sessions * 0.5).round(),
                "key_events": starts.round(), "starts_true": starts,
                "conversions": 0.0, "video_views": 0.0}))
    return pd.concat(rows, ignore_index=True)


def _ground_truth(spec, paid, spend_grid, contribution, beta, channels, baseline,
                  submitted, weeks, geos) -> dict:
    """Per-channel true contribution + ROI + baseline share; exact identity components."""
    by_channel = {}
    for ch in paid:
        spend = float(spend_grid[ch].sum())
        contrib = float(contribution[ch].sum())
        by_channel[ch] = {
            "spend": round(spend, 2),
            "true_incremental_submitted": round(contrib, 2),
            "roi_apps_per_1k": round(contrib / spend * 1000.0, 4) if spend else 0.0,
            "beta": round(beta[ch], 6),
            "roi_posture": channels[ch].get("roi_posture", ""),
        }
    # Identity components from UNROUNDED floats so the residual is exact (== 0 by
    # construction: submitted = baseline + Sigma contribution). The per-channel display
    # figures above are rounded for readability; the identity check must not be.
    paid_contrib = float(sum(contribution[ch].sum() for ch in paid))
    base = float(baseline.sum()); kpi = float(submitted.sum())
    rank = sorted(by_channel, key=lambda c: by_channel[c]["roi_apps_per_1k"], reverse=True)
    return {
        "scenario": spec["name"],
        "mmm_target": spec["meta"]["mmm_target"],
        "identity": {
            "baseline": round(base, 2),
            "paid_contribution": round(paid_contrib, 2),
            "kpi_submitted": round(kpi, 2),
            "residual": kpi - base - paid_contrib,             # == 0 by construction
            "paid_share": round(paid_contrib / kpi, 4) if kpi else 0.0,
        },
        "roi_rank_order": rank,
        "by_channel": by_channel,
        "weeks": len(weeks), "geos": [g["code"] for g in geos],
    }


def _pipeline(spec, weeks, geo_codes, init_codes, submitted, spend_grid, contribution,
              geo_mult, init_share, paid) -> pd.DataFrame:
    """Flow submitted-by-(week,geo,initiative) cohorts through the 6 gates, emitting
    right-censored CALENDAR-week stage counts (+ last-touch channel)."""
    cfg = spec["pipeline"]
    stages = cfg["stages"]
    paths = cfg["paths"]
    n_w = len(weeks)
    week_index = {w: i for i, w in enumerate(weeks)}

    # submitted split by initiative (spend-by-initiative weights; baseline ~ even)
    iw = np.array([init_share[c] for c in init_codes]); iw = iw / iw.sum()

    # last-touch channel weights per initiative (paid contribution share + an organic slug)
    ch_share = {ch: float(contribution[ch].sum()) for ch in paid}
    tot = sum(ch_share.values()) or 1.0
    ch_share = {ch: v / tot for ch, v in ch_share.items()}
    channels_lt = list(ch_share) + ["organic"]
    ch_probs = np.array([ch_share[c] * 0.65 for c in paid] + [0.35])

    records: dict[tuple, float] = {}
    for gi, code in enumerate(geo_codes):
        for ii, icode in enumerate(init_codes):
            path = paths.get(icode, paths["default"]) if icode in paths else paths["default"]
            pr = path["pass_rate"]; lag = path["lag_weeks"]
            weekly_sub = submitted[:, gi] * iw[ii]
            cum = 1.0
            for stage in stages:
                cum *= float(pr[stage])
                lo, hi = int(lag[stage][0]), int(lag[stage][1])
                span = max(hi - lo + 1, 1)
                for wi in range(n_w):
                    arrivals = weekly_sub[wi] * cum
                    if arrivals <= 0:
                        continue
                    per_wk = arrivals / span
                    for d in range(lo, hi + 1):
                        cal = wi + d
                        if cal >= n_w:               # right-censored: beyond the flight edge
                            continue
                        for ci, chan in enumerate(channels_lt):
                            key = (weeks[cal], code, icode, stage, chan)
                            records[key] = records.get(key, 0.0) + per_wk * ch_probs[ci]

    if not records:
        return pd.DataFrame(columns=["date", "geo", "initiative", "stage", "channel", "count"])
    idx = list(records)
    out = pd.DataFrame(idx, columns=["date", "geo", "initiative", "stage", "channel"])
    out["count"] = [records[k] for k in idx]
    out["count"] = out["count"].round(2)
    return out.sort_values(["date", "geo", "initiative", "stage", "channel"]).reset_index(drop=True)
