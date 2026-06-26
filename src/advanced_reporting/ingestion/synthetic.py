"""Synthetic data source — the ACTIVE ad source today.

Wraps our known-ground-truth data-generating process (DGP) and emits rows in the
canonical daily schema (date x channel x campaign x geo), so it exercises the EXACT
code path a real platform pull will use: ``fetch(start, end)`` -> ``schema.to_canonical``
-> downstream cleanse/model. The same DGP functions back ``scripts/generate_sample_data.py``
(single source of truth), so the on-disk CSVs and this source never drift.

Channels are deliberately decorrelated (distinct seasonal phases + random flighting) so the
MMM has a fair chance to separate them. Business-KPI revenue is built from the SAME weekly
per-channel spend used here, so ad spend and KPI stay consistent regardless of how spend is
split across campaigns/days/geos (splits always sum to 1, preserving weekly totals).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import DataSource
from . import schema
from ..mmm.transforms import geometric_adstock, hill_saturation
from ..utils import load_mappings

N_WEEKS = 104
END_DATE = "2026-06-22"
# A handful of geos. Currency stays USD for the synthetic stand-in; a real connector sets
# currency per geo from request params (schema.normalize fills the default otherwise).
DEFAULT_GEOS = ["US-NE", "US-MW", "US-W"]

# channel -> data-generating parameters (weekly scale, USD). phase decorrelates channels.
CHANNELS = {
    #               base_spend seas  phase trend decay  half_sat slope scale(max wk contrib)
    "google_search": dict(base=40000, seas=0.20, phase=0.0, trend=0.15, decay=0.30, half=30000, slope=1.0, scale=320000),
    "google_pmax":   dict(base=30000, seas=0.25, phase=1.2, trend=0.35, decay=0.40, half=28000, slope=1.2, scale=220000),
    "meta":          dict(base=50000, seas=0.30, phase=2.6, trend=0.10, decay=0.50, half=55000, slope=1.3, scale=260000),
    "tiktok":        dict(base=20000, seas=0.45, phase=4.0, trend=0.60, decay=0.60, half=18000, slope=1.5, scale=120000),
    "linkedin":      dict(base=8000,  seas=0.15, phase=5.2, trend=0.05, decay=0.45, half=7000,  slope=1.1, scale=45000),
}

FUNNEL = {
    "google_search": dict(cpm=12, ctr=0.050, cvr=0.060, aov=120),
    "google_pmax":   dict(cpm=9,  ctr=0.012, cvr=0.030, aov=110),
    "meta":          dict(cpm=7,  ctr=0.009, cvr=0.018, aov=95),
    "tiktok":        dict(cpm=5,  ctr=0.007, cvr=0.012, aov=80),
    "linkedin":      dict(cpm=25, ctr=0.004, cvr=0.025, aov=320),
}

CAMPAIGNS = {
    "google_search": [("brand", 0.35), ("nonbrand", 0.65)],
    "google_pmax":   [("pmax_main", 1.0)],
    "meta":          [("prospecting", 0.6), ("retargeting", 0.4)],
    "tiktok":        [("tiktok_awareness", 1.0)],
    "linkedin":      [("linkedin_abm", 1.0)],
}

# Mid-funnel / web-analytics behaviour by channel (the GA4-style middle tier).
#   landing : share of clicks that become sessions   engaged : share of sessions engaged
#   ppv     : page views per session                 video   : video views per session
#   eng_sec : avg engagement seconds per session
ENGAGEMENT = {
    "google_search": dict(landing=0.90, engaged=0.65, ppv=2.6, video=0.00, eng_sec=70),
    "google_pmax":   dict(landing=0.80, engaged=0.55, ppv=2.0, video=0.25, eng_sec=45),
    "meta":          dict(landing=0.75, engaged=0.50, ppv=1.8, video=0.40, eng_sec=35),
    "tiktok":        dict(landing=0.70, engaged=0.45, ppv=1.5, video=0.80, eng_sec=25),
    "linkedin":      dict(landing=0.85, engaged=0.60, ppv=2.2, video=0.05, eng_sec=55),
}


def weekly_spend(p: dict, t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Seasonal (phase-shifted) + trending + pulsed + noisy weekly spend for one channel."""
    season = 1 + p["seas"] * np.sin(2 * np.pi * (t / 52.0) + p["phase"])
    trend = 1 + p["trend"] * (t / N_WEEKS)
    noise = np.clip(rng.normal(1.0, 0.15, size=len(t)), 0.4, None)
    spend = p["base"] * season * trend * noise
    # random campaign pulses (flighting): ~12 weeks get a budget burst
    pulse_weeks = rng.choice(len(t), size=12, replace=False)
    spend[pulse_weeks] *= rng.uniform(1.3, 1.9, size=12)
    return np.clip(spend, 0, None)


def simulate_weekly(rng: np.random.Generator):
    """Weekly per-channel spend + true contribution + ground-truth ROI.

    Returns ``(weeks, t, spend_wk, contrib_wk, truth)``. Drawn FIRST so both the ad frame
    and the KPI frame share the same ``spend_wk`` basis.
    """
    weeks = pd.date_range(end=END_DATE, periods=N_WEEKS, freq="W-MON")
    t = np.arange(N_WEEKS)
    spend_wk, contrib_wk, truth = {}, {}, {}
    for ch, p in CHANNELS.items():
        s = weekly_spend(p, t, rng)
        adstocked = geometric_adstock(s, p["decay"], max_lag=8)
        sat = hill_saturation(adstocked, p["half"], p["slope"])
        contrib = p["scale"] * sat
        spend_wk[ch] = s
        contrib_wk[ch] = contrib
        truth[ch] = dict(total_spend=float(s.sum()),
                         total_contribution=float(contrib.sum()),
                         roi=float(contrib.sum() / max(s.sum(), 1.0)))
    return weeks, t, spend_wk, contrib_wk, truth


def _add_engagement(ad: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Add GA4-style mid-funnel metrics, derived from clicks per channel.

    Vectorized and run AFTER the main loop (channels still clean), so it does not shift the
    per-row ad-metric draws — existing spend/impressions/clicks/conversions are unchanged.
    """
    n = len(ad)
    p = {k: ad["channel"].map({c: ENGAGEMENT[c][k] for c in ENGAGEMENT}).to_numpy(float)
         for k in ("landing", "engaged", "ppv", "video", "eng_sec")}
    clicks = ad["clicks"].to_numpy(float)
    sessions = np.clip(clicks * p["landing"] * rng.normal(1, 0.10, n), 0, None)
    ad["sessions"] = np.round(sessions)
    ad["engaged_sessions"] = np.round(sessions * p["engaged"])
    ad["page_views"] = np.round(sessions * p["ppv"])
    ad["video_views"] = np.round(sessions * p["video"])
    ad["avg_engagement_seconds"] = np.round(np.clip(p["eng_sec"] * rng.normal(1, 0.10, n), 0, None), 1)
    return ad


def _inject_mess(ad: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Inject realistic imperfections so the cleansing layer earns its keep."""
    mask = rng.random(len(ad)) < 0.04
    ad.loc[mask, "channel"] = ad.loc[mask, "channel"].str.upper()
    alias = rng.random(len(ad)) < 0.01
    ad.loc[alias & (ad["channel"].str.lower() == "meta"), "channel"] = "facebook"
    nan_idx = rng.choice(ad.index, size=int(len(ad) * 0.01), replace=False)
    ad.loc[nan_idx, "spend"] = np.nan
    neg_idx = rng.choice(ad.index, size=12, replace=False)
    ad.loc[neg_idx, "spend"] = -ad.loc[neg_idx, "spend"].abs().fillna(50)
    dups = ad.sample(frac=0.01, random_state=1)
    ad = pd.concat([ad, dups], ignore_index=True).sample(frac=1, random_state=2).reset_index(drop=True)
    return ad


def build_ad_frame(weeks, spend_wk, geos, rng: np.random.Generator, *,
                   messy: bool = True) -> pd.DataFrame:
    """Expand weekly per-channel spend into granular daily x campaign x geo rows.

    Campaign shares, day-of-week weights and geo weights each sum to 1, so daily rows sum
    back to ``spend_wk`` per channel-week (keeping the KPI coupling intact).
    """
    geos = list(geos)
    n_geo = len(geos)
    rows = []
    for wi, wk in enumerate(weeks):
        for ch, camps in CAMPAIGNS.items():
            f = FUNNEL[ch]
            for camp, share in camps:
                cw_spend = spend_wk[ch][wi] * share
                day_w = rng.dirichlet(np.ones(7) * 6)
                for d in range(7):
                    day_spend = cw_spend * day_w[d]
                    geo_w = rng.dirichlet(np.ones(n_geo) * 8) if n_geo > 1 else np.array([1.0])
                    date_iso = (wk + pd.Timedelta(days=d)).date().isoformat()
                    for gi, geo in enumerate(geos):
                        spend = day_spend * geo_w[gi]
                        impr = spend / f["cpm"] * 1000
                        ctr = max(f["ctr"] * rng.normal(1, 0.15), 0.0005)
                        clicks = impr * ctr
                        cvr = max(f["cvr"] * rng.normal(1, 0.15), 0.001)
                        conv = clicks * cvr
                        rows.append({"date": date_iso, "channel": ch, "campaign": camp,
                                     "geo": geo, "spend": round(spend, 2),
                                     "impressions": int(impr), "clicks": int(clicks),
                                     "conversions": round(conv, 2),
                                     "platform_revenue": round(conv * f["aov"], 2)})
    ad = _add_engagement(pd.DataFrame(rows), rng)        # mid-funnel tier (channels still clean)
    return _inject_mess(ad, rng) if messy else ad


def build_kpi_frame(weeks, t, contrib_wk, rng: np.random.Generator) -> pd.DataFrame:
    """Weekly business revenue + control variables, driven by the media contribution."""
    price_index = 100 + rng.normal(0, 3, N_WEEKS).cumsum() * 0.3
    promo_flag = (rng.random(N_WEEKS) < 0.12).astype(int)
    baseline = 300000 + 1200 * t + 80000 * np.sin(2 * np.pi * (t / 52.0))
    promo_effect = 90000 * promo_flag
    price_effect = -4000 * (price_index - 100)
    media_total = np.sum(list(contrib_wk.values()), axis=0)
    noise = rng.normal(0, 40000, N_WEEKS)
    revenue = np.clip(baseline + media_total + promo_effect + price_effect + noise, 0, None)
    return pd.DataFrame({"date": weeks, "revenue": np.round(revenue, 2),
                         "price_index": np.round(price_index, 2), "promo_flag": promo_flag})


def _filter_dates(df: pd.DataFrame, start, end) -> pd.DataFrame:
    """Inclusive [start, end] filter on the ISO ``date`` column (no-op if both None)."""
    if start is None and end is None:
        return df
    d = pd.to_datetime(df["date"], errors="coerce")
    mask = pd.Series(True, index=df.index)
    if start is not None:
        mask &= d >= pd.Timestamp(start)
    if end is not None:
        mask &= d <= pd.Timestamp(end)
    return df[mask].reset_index(drop=True)


class SyntheticSource(DataSource):
    """In-memory synthetic ad source emitting the canonical daily schema."""

    name = "synthetic"
    source = "default"   # rows already use canonical column names -> identity map

    def __init__(self, geos=None, seed: int = 42, currency: str = "USD",
                 messy: bool = True):
        self.geos = list(geos) if geos else list(DEFAULT_GEOS)
        self.seed = seed
        self.currency = currency
        self.messy = messy

    def fetch(self, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        rng = np.random.default_rng(self.seed)
        weeks, _t, spend_wk, _contrib, _truth = simulate_weekly(rng)
        ad = build_ad_frame(weeks, spend_wk, self.geos, rng, messy=self.messy)
        ad = schema.to_canonical(ad, self.source, load_mappings(), currency=self.currency)
        return _filter_dates(ad, start, end)
