"""Deterministic evidence tools — compact, structured, with provenance + confidence.

The planner feeds the LLM *only* this evidence (never raw store rows or full tables), so the
context stays small and the cost stays tracked. Three kinds:

* ``historical_performance`` — CPA / ROAS / CVR per channel from the durable store.
* ``response_curves`` — per-channel incremental return + saturation from a fitted MMM. This is
  the **first-party, incrementality-grounded** input. Platform forecasts (below) are
  walled-garden / self-credited, so they're for reach/feasibility only — never cross-channel
  allocation.
* ``platform_forecasts`` — reach/conversion forecasters behind the connector pattern; all
  stubs that ``raise NotImplementedError`` with exact wiring notes.

Demographic grounding ("Meta for 35+") needs an audience/demographic breakdown the canonical
schema doesn't carry — ``historical_performance_by_demo`` is a clearly-flagged stub naming the
``ingestion/schema.py`` extension as the dependency.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..ingestion.store import read_history


@dataclass
class Evidence:
    """A compact, structured evidence packet (LLM-ready) with provenance + confidence."""
    kind: str                       # "historical" | "response_curves" | "forecast"
    data: dict
    provenance: str = ""
    confidence: float = 0.0
    notes: str = ""
    refs: list[str] = field(default_factory=list)


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


# --- first-party evidence from the durable store --------------------------------------

def historical_performance(history=None, by=("channel",), *, reader=read_history) -> Evidence:
    """CPA / ROAS / CVR per ``by`` group (default: per channel) from ``history.parquet``.

    Metrics are ratios of summed totals (CPA = sum(spend)/sum(conversions), ROAS =
    sum(platform_revenue)/sum(spend), CVR = sum(conversions)/sum(clicks)).
    """
    df = history if history is not None else reader()
    cols = list(by)
    need = ["spend", "conversions", "platform_revenue", "clicks"]
    present = [c for c in need if c in df.columns]
    sums = df.groupby(cols, as_index=False)[present].sum()

    data: dict = {}
    for _, r in sums.iterrows():
        key = str(r[cols[0]]) if len(cols) == 1 else tuple(str(r[c]) for c in cols)
        spend, conv = _f(r.get("spend", 0)), _f(r.get("conversions", 0))
        rev, clk = _f(r.get("platform_revenue", 0)), _f(r.get("clicks", 0))
        data[key] = {
            "spend": spend, "conversions": conv, "platform_revenue": rev,
            "cpa": (spend / conv) if conv else float("nan"),
            "roas": (rev / spend) if spend else float("nan"),
            "cvr": (conv / clk) if clk else float("nan"),
        }
    prov = f"store:history.parquet by {'/'.join(cols)} ({len(df)} rows)"
    return Evidence("historical", data, provenance=prov,
                    confidence=min(1.0, len(df) / 100.0),
                    refs=[f"historical:{'/'.join(cols)}"])


def historical_performance_by_demo(*args, **kwargs):
    """Demo-level grounding — NOT available yet (a clearly-flagged dependency, not silent).

    CPA/ROAS/CVR *by demographic/audience segment* needs age/gender/audience columns the
    canonical daily schema doesn't carry (see ``ingestion/schema.py:CANONICAL_COLUMNS`` — no
    demographic fields). Wiring this is a schema extension: add the breakdown columns to
    ``ingestion/schema.py`` (and populate them in the sources), then aggregate here the same
    way ``historical_performance`` does, grouping by the new segment column(s).
    """
    raise NotImplementedError(
        "historical_performance_by_demo needs a demographic/audience breakdown the canonical "
        "schema doesn't carry yet. Extend ingestion/schema.py (add e.g. an 'audience'/'age' "
        "column to CANONICAL_COLUMNS + the sources), then group by it here. Until then, "
        "ground audience choices on the rails' audience_library, not measured demo data."
    )


# --- first-party incrementality from the fitted MMM -----------------------------------

def response_curves(mmm_result) -> Evidence:
    """Per-channel incremental return + saturation from a fitted ``MMMResult``.

    For each channel: the marginal return d(response)/d(spend) at mean spend, the saturation
    spend (where response reaches ~90% of its modelled max), and ROI with its 90% interval
    from ``channel_summary`` (uncertainty-aware). Reuses the curves the MMM already built from
    Hill saturation (``mmm/transforms.py``).
    """
    cs = getattr(mmm_result, "channel_summary", None)
    roi_by: dict = {}
    if cs is not None and "channel" in getattr(cs, "columns", []):
        roi_by = cs.set_index("channel").to_dict("index")

    data: dict = {}
    for ch, c in (mmm_result.response_curves or {}).items():
        spend = np.asarray(c["spend"], dtype=float)
        resp = np.asarray(c["response"], dtype=float)
        mean_spend = _f(c.get("mean_spend", spend.mean() if spend.size else 0.0))
        deriv = np.gradient(resp, spend) if spend.size > 1 else np.array([0.0])
        marginal = float(np.interp(mean_spend, spend, deriv)) if spend.size else 0.0
        rmax = float(resp.max()) if resp.size else 0.0
        sat_idx = int(np.argmax(resp >= 0.9 * rmax)) if rmax > 0 else 0
        rec = {
            "mean_spend": mean_spend,
            "marginal_return_at_mean": marginal,
            "saturation_spend": float(spend[sat_idx]) if spend.size else float("nan"),
            "max_response": rmax,
        }
        r = roi_by.get(ch, {})
        rec.update(roi=_f(r.get("roi")), roi_low=_f(r.get("roi_low")), roi_high=_f(r.get("roi_high")))
        data[ch] = rec

    return Evidence("response_curves", data,
                    provenance=f"mmm:{getattr(mmm_result, 'engine', 'unknown')}",
                    confidence=0.8, refs=[f"response_curves:{ch}" for ch in data])


# --- platform forecasts (reach/feasibility ONLY) — connector-style stubs ---------------

class PlatformForecaster:
    """Base contract for a platform reach/conversion forecaster.

    NOTE: platform numbers are walled-garden and self-credited. Use them for reach and
    feasibility checks only — NEVER for cross-channel budget allocation (that is the MMM's
    incrementality-grounded job in ``allocator.py``).
    """
    name = "base"

    def forecast(self, audience, budget, *, start=None, end=None) -> Evidence:
        raise NotImplementedError


class GoogleAdsForecaster(PlatformForecaster):
    """Google Ads reach/performance forecaster (skeleton).

    Credentials (.env): GOOGLE_ADS_DEVELOPER_TOKEN, GOOGLE_ADS_CLIENT_ID,
        GOOGLE_ADS_CLIENT_SECRET, GOOGLE_ADS_REFRESH_TOKEN, GOOGLE_ADS_LOGIN_CUSTOMER_ID.
    Endpoint: ReachPlanService.GenerateReachForecast (video/display reach curves) and/or
        PerformancePlannerService for search conversion forecasts at a spend point. Verify the
        current service/method names against the installed google-ads version at build time.
    """
    name = "google_ads"

    def forecast(self, audience, budget, *, start=None, end=None) -> Evidence:
        raise NotImplementedError(
            "GoogleAdsForecaster is a skeleton. Authenticate via the Google Ads creds, call "
            "ReachPlanService.GenerateReachForecast / PerformancePlannerService for the "
            "audience+budget, then return Evidence(kind='forecast', ...). Reach/feasibility "
            "only — not for cross-channel allocation."
        )


class MetaForecaster(PlatformForecaster):
    """Meta reach & delivery estimate forecaster (skeleton).

    Credentials (.env): META_ACCESS_TOKEN, META_AD_ACCOUNT_ID.
    Endpoint: Graph API delivery_estimate / reachestimate on the ad account for the targeting
        spec + budget (reach, impressions, estimated actions). Verify endpoint names at build.
    """
    name = "meta"

    def forecast(self, audience, budget, *, start=None, end=None) -> Evidence:
        raise NotImplementedError(
            "MetaForecaster is a skeleton. Authenticate via META creds, call the delivery/reach "
            "estimate endpoint for the targeting spec + budget, then return Evidence. "
            "Reach/feasibility only."
        )


class TikTokForecaster(PlatformForecaster):
    """TikTok audience-size / reach forecaster (skeleton).

    Credentials (.env): TIKTOK_ACCESS_TOKEN, TIKTOK_ADVERTISER_ID.
    Endpoint: audience size estimate / reach forecast on the advertiser for the targeting +
        budget. Verify the current endpoint names at build time.
    """
    name = "tiktok"

    def forecast(self, audience, budget, *, start=None, end=None) -> Evidence:
        raise NotImplementedError(
            "TikTokForecaster is a skeleton. Authenticate via TIKTOK creds, call the audience "
            "size / reach forecast endpoint, then return Evidence. Reach/feasibility only."
        )


class LinkedInForecaster(PlatformForecaster):
    """LinkedIn audience-forecast forecaster (skeleton).

    Credentials (.env): LINKEDIN_ACCESS_TOKEN, LINKEDIN_AD_ACCOUNT_ID.
    Endpoint: /rest/audienceCounts (or the current forecast endpoint) for the targeting
        criteria; combine with budget for a reach/feasibility estimate. Verify at build time.
    """
    name = "linkedin"

    def forecast(self, audience, budget, *, start=None, end=None) -> Evidence:
        raise NotImplementedError(
            "LinkedInForecaster is a skeleton. Authenticate via LINKEDIN creds, call the "
            "audience forecast endpoint, then return Evidence. Reach/feasibility only."
        )


_FORECASTERS = {
    "google_ads": GoogleAdsForecaster, "google_search": GoogleAdsForecaster,
    "google_pmax": GoogleAdsForecaster, "meta": MetaForecaster,
    "tiktok": TikTokForecaster, "linkedin": LinkedInForecaster,
}


def get_forecaster(name: str | None = None, **kwargs) -> PlatformForecaster:
    """Select a platform forecaster by channel name (mirrors ``ingestion/factory.py``)."""
    key = (name or "").lower()
    cls = _FORECASTERS.get(key)
    if cls is None:
        raise ValueError(
            f"Unknown forecaster '{name}'. Use one of: {', '.join(sorted(set(_FORECASTERS)))}."
        )
    return cls(**kwargs)
