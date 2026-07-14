"""Aggregations for the drill-down pages (redesign R3/R4) — pure pandas, testable.

Reads the canonical daily history (campaign + ad_group grain with the decoded naming
fields) and rolls it up for the Channels / Audiences pages.

HONESTY RULE (enforced here, labeled on the pages): GA4 key events exist at CAMPAIGN
grain only — audience- and creative-level conversions are PLATFORM-CLAIMED only.
``key_events`` therefore appears exclusively on campaign-level rollups; the ad-group /
audience / creative tables expose claimed conversions and cost-per-claimed, never
implying GA4 verification below campaign grain.
"""
from __future__ import annotations

import pandas as pd

from ..ingestion.naming_decode import UNPARSED

_METRICS = ["spend", "impressions", "clicks", "conversions"]


def _weekly_key(dates: pd.Series) -> pd.Series:
    dates = pd.to_datetime(dates)
    return dates - pd.to_timedelta(dates.dt.weekday, unit="D")


def _ad_rows(hist: pd.DataFrame) -> pd.DataFrame:
    """Rows from ad platforms (spend measured); web-analytics rows have NaN spend."""
    return hist[hist["spend"].notna()]


def campaign_table(hist: pd.DataFrame) -> pd.DataFrame:
    """Per channel × campaign: delivery + claimed conversions + campaign-level GA4
    key events (joined on channel+campaign) + cost columns."""
    ads = _ad_rows(hist)
    per = (ads.groupby(["channel", "campaign"], as_index=False)[_METRICS]
              .sum(min_count=1))
    if "key_events" in hist.columns:
        ke = (hist.groupby(["channel", "campaign"], as_index=False)["key_events"]
                  .sum(min_count=1))
        per = per.merge(ke, on=["channel", "campaign"], how="left")
    else:
        per["key_events"] = float("nan")
    per["cost_per_claimed"] = per["spend"] / per["conversions"].replace(0, pd.NA)
    per["cost_per_key_event"] = per["spend"] / per["key_events"].replace(0, pd.NA)
    return per.sort_values(["channel", "spend"], ascending=[True, False]) \
              .reset_index(drop=True)


def ad_group_table(hist: pd.DataFrame) -> pd.DataFrame:
    """Per channel × campaign × ad_group with the decoded naming fields.

    Claimed-only by design (no key_events column — see the module honesty rule).
    """
    ads = _ad_rows(hist)
    ads = ads[ads["ad_group"].fillna("") != ""]
    if ads.empty:
        return pd.DataFrame(columns=["channel", "campaign", "ad_group", "audience_type",
                                     "audience_detail", "creative", "creative_format",
                                     *_METRICS, "cost_per_claimed"])
    keys = ["channel", "campaign", "ad_group", "audience_type", "audience_detail",
            "creative", "creative_format"]
    per = ads.groupby(keys, as_index=False)[_METRICS].sum(min_count=1)
    per["cost_per_claimed"] = per["spend"] / per["conversions"].replace(0, pd.NA)
    return per.sort_values(["channel", "campaign", "spend"],
                           ascending=[True, True, False]).reset_index(drop=True)


def audience_summary(hist: pd.DataFrame) -> pd.DataFrame:
    """Audience_type × audience_detail rollup with spend/claimed shares (R4).

    Rows whose names decoded to CREATIVE fields only (e.g. LinkedIn creatives) carry
    no audience label and are excluded; "(unparsed)" stays in as its own honest bucket.
    """
    ag = ad_group_table(hist)
    ag = ag[ag["audience_type"] != ""]
    if ag.empty:
        return pd.DataFrame(columns=["audience_type", "audience_detail", "channel",
                                     *_METRICS, "cost_per_claimed", "spend_share",
                                     "claimed_share"])
    per = (ag.groupby(["audience_type", "audience_detail"], as_index=False)
             .agg(channel=("channel", lambda s: ", ".join(sorted(set(s)))),
                  spend=("spend", "sum"), impressions=("impressions", "sum"),
                  clicks=("clicks", "sum"), conversions=("conversions", "sum")))
    per["cost_per_claimed"] = per["spend"] / per["conversions"].replace(0, pd.NA)
    per["spend_share"] = per["spend"] / per["spend"].sum()
    per["claimed_share"] = per["conversions"] / per["conversions"].sum()
    return per.sort_values("cost_per_claimed").reset_index(drop=True)


def creative_summary(hist: pd.DataFrame) -> pd.DataFrame:
    """Creative × format rollup (from Ad-grammar names; claimed-only)."""
    ag = ad_group_table(hist)
    ag = ag[ag["creative"] != ""]
    if ag.empty:
        return pd.DataFrame(columns=["creative", "creative_format", "channel",
                                     *_METRICS, "cost_per_claimed"])
    per = (ag.groupby(["creative", "creative_format"], as_index=False)
             .agg(channel=("channel", lambda s: ", ".join(sorted(set(s)))),
                  spend=("spend", "sum"), impressions=("impressions", "sum"),
                  clicks=("clicks", "sum"), conversions=("conversions", "sum")))
    per["cost_per_claimed"] = per["spend"] / per["conversions"].replace(0, pd.NA)
    return per.sort_values("cost_per_claimed").reset_index(drop=True)


def audience_weekly(hist: pd.DataFrame, top_n: int = 4) -> pd.DataFrame:
    """Weekly claimed conversions for the top-``top_n`` audiences by spend (R4 trend).

    Long frame: (date, audience, conversions, spend) — audience = "TYPE · DETAIL".
    """
    ads = _ad_rows(hist)
    ads = ads[(ads["ad_group"].fillna("") != "") & (ads["audience_type"] != "")
              & (ads["audience_type"] != UNPARSED)].copy()
    if ads.empty:
        return pd.DataFrame(columns=["date", "audience", "conversions", "spend"])
    ads["audience"] = ads["audience_type"] + " · " + ads["audience_detail"]
    top = (ads.groupby("audience")["spend"].sum()
              .sort_values(ascending=False).head(top_n).index)
    ads = ads[ads["audience"].isin(top)]
    ads["date"] = _weekly_key(ads["date"])
    return (ads.groupby(["date", "audience"], as_index=False)
               [["conversions", "spend"]].sum().sort_values(["date", "audience"])
               .reset_index(drop=True))


def unparsed_stats(hist: pd.DataFrame) -> dict:
    """The unparsed-name bucket, measured in rows AND spend (the adoption pitch)."""
    ads = _ad_rows(hist)
    ad_level = ads[ads["ad_group"].fillna("") != ""]
    if ad_level.empty:
        return {"row_rate": 0.0, "spend_rate": 0.0, "names": [], "spend": 0.0}
    unp = ad_level[ad_level["audience_type"] == UNPARSED]
    total_spend = float(ad_level["spend"].sum())
    return {
        "row_rate": len(unp) / len(ad_level),
        "spend_rate": float(unp["spend"].sum()) / total_spend if total_spend else 0.0,
        "names": sorted(unp["ad_group"].unique()),
        "spend": float(unp["spend"].sum()),
    }


def creative_initiative_table(hist: pd.DataFrame) -> pd.DataFrame:
    """Creative x career-path (initiative) rollup — "what messaging works where".

    The initiative is decoded from the campaign name's optional trailing segment;
    creative fields exist only where ad names follow the Ad grammar (LinkedIn in the
    FBI dataset — the caption on the page says so, and pitches extending the grammar).
    Claimed-only by the module honesty rule.
    """
    from ..ingestion.naming_decode import decode_campaign_name

    ag = ad_group_table(hist)
    ag = ag[ag["creative"] != ""].copy()
    if ag.empty:
        return pd.DataFrame(columns=["creative", "creative_format", "initiative",
                                     *_METRICS, "cost_per_claimed"])
    ag["initiative"] = ag["campaign"].map(
        lambda c: decode_campaign_name(c).initiative or "(none)")
    per = (ag.groupby(["creative", "creative_format", "initiative"], as_index=False)
             [_METRICS].sum(min_count=1))
    per["cost_per_claimed"] = per["spend"] / per["conversions"].replace(0, pd.NA)
    return per.sort_values(["initiative", "cost_per_claimed"]).reset_index(drop=True)


def geo_summary(hist: pd.DataFrame, kpi: pd.DataFrame | None = None,
                populations: dict | None = None) -> pd.DataFrame:
    """Per-geo rollup for the Geography page.

    Columns: geo, spend (paid), key_events (GA4-measured, ALL traffic), start_share,
    plus submitted_applications when the CRM KPI frame is given, and pop_share /
    vs_population (start share / population share) when population weights exist.
    ``vs_population`` reads as an over/under-index: 1.0 = starts proportional to
    population; provenance stays descriptive (never causal).
    """
    d = hist[hist["geo"].fillna("") != ""]
    if d.empty:
        return pd.DataFrame(columns=["geo", "spend", "key_events", "start_share"])
    per = (d.groupby("geo")
             .agg(spend=("spend", "sum"), key_events=("key_events", "sum"))
             .reset_index())
    total = float(per["key_events"].sum())
    per["start_share"] = per["key_events"] / total if total else pd.NA
    if kpi is not None and {"geo", "submitted_applications"}.issubset(kpi.columns):
        sub = (kpi.groupby("geo", as_index=False)["submitted_applications"].sum())
        per = per.merge(sub, on="geo", how="left")
    if populations:
        per["pop_share"] = per["geo"].map(populations)
        per["vs_population"] = per["start_share"] / per["pop_share"]
    return per.sort_values("key_events", ascending=False).reset_index(drop=True)
