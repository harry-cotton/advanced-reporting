"""Real-platform connector skeletons (Phase 2 — not yet wired).

Each class implements the ``DataSource`` contract but ``fetch()`` raises
``NotImplementedError``. Automating a platform later is "fill in the API call": authenticate
with ``self.require_credentials(...)`` (reads ``.env`` / env, never hardcoded), pull the
date-ranged report with pagination, wrap the HTTP call in ``self.with_retries(...)``, then
hand the raw frame to ``schema.to_canonical(df, self.source, load_mappings())`` — the
``self.source`` key selects that platform's column map in ``config/mappings.yaml``.

No SDKs are imported here, so importing this module is cheap and dependency-free.
"""
from __future__ import annotations

import pandas as pd

from .base import DataSource


class GoogleAdsSource(DataSource):
    """Google Ads API connector (skeleton).

    Credentials (.env): GOOGLE_ADS_DEVELOPER_TOKEN, GOOGLE_ADS_CLIENT_ID,
        GOOGLE_ADS_CLIENT_SECRET, GOOGLE_ADS_REFRESH_TOKEN, GOOGLE_ADS_LOGIN_CUSTOMER_ID.
    Endpoint: GoogleAdsService.SearchStream with a GAQL query over `campaign` + `metrics`
        (metrics.cost_micros, impressions, clicks, conversions, conversions_value),
        segmented by segments.date.
    Date range / pagination: `WHERE segments.date BETWEEN '{start}' AND '{end}'`; SearchStream
        returns batched pages — iterate all batches (cost is in micros: divide by 1e6).
    Mapping: source key 'google_ads' in config/mappings.yaml (Cost->spend, Impr.->impressions,
        Conv. value->platform_revenue, ...); set `channel` per campaign's advertising channel
        type; geo from segments.geo_target / account; then schema.to_canonical(...).
    """

    name = "google_ads"
    source = "google_ads"

    def __init__(self, customer_id: str | None = None):
        self.customer_id = customer_id

    def fetch(self, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        raise NotImplementedError(
            "GoogleAdsSource is a Phase-2 skeleton. Authenticate via require_credentials("
            "'GOOGLE_ADS_DEVELOPER_TOKEN', 'GOOGLE_ADS_CLIENT_ID', 'GOOGLE_ADS_CLIENT_SECRET', "
            "'GOOGLE_ADS_REFRESH_TOKEN', 'GOOGLE_ADS_LOGIN_CUSTOMER_ID'), run the GAQL "
            "SearchStream report for [start, end], then schema.to_canonical(df, 'google_ads', "
            "load_mappings())."
        )


class MetaSource(DataSource):
    """Meta (Facebook/Instagram) Marketing API connector (skeleton).

    Credentials (.env): META_ACCESS_TOKEN, META_APP_ID, META_APP_SECRET, META_AD_ACCOUNT_ID.
    Endpoint: Graph API GET /{ad_account_id}/insights with level=campaign and fields
        [campaign_name, spend, impressions, link_clicks (inline_link_clicks), actions,
        action_values].
    Date range / pagination: time_range={'since': start, 'until': end}; follow cursor paging
        via `paging.next` until exhausted. `actions`/`action_values` are typed lists — pick the
        purchase/conversion action type.
    Mapping: source key 'meta' in config/mappings.yaml (spend->spend, link_clicks->clicks,
        action_values->platform_revenue, ...); set `channel`='meta'; then schema.to_canonical.
    """

    name = "meta"
    source = "meta"

    def __init__(self, ad_account_id: str | None = None):
        self.ad_account_id = ad_account_id

    def fetch(self, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        raise NotImplementedError(
            "MetaSource is a Phase-2 skeleton. Authenticate via require_credentials("
            "'META_ACCESS_TOKEN', 'META_AD_ACCOUNT_ID'), GET /{ad_account_id}/insights "
            "(level=campaign, time_range=[start, end], cursor paging), then "
            "schema.to_canonical(df, 'meta', load_mappings())."
        )


class TikTokSource(DataSource):
    """TikTok Ads reporting API connector (skeleton).

    Credentials (.env): TIKTOK_ACCESS_TOKEN, TIKTOK_ADVERTISER_ID, TIKTOK_APP_ID,
        TIKTOK_SECRET.
    Endpoint: POST/GET /open_api/v1.3/report/integrated/get/ with report_type=BASIC,
        data_level=AUCTION_CAMPAIGN, dimensions=[campaign_id, stat_time_day], metrics
        [spend, impressions, clicks, conversions, total_complete_payment].
    Date range / pagination: start_date/end_date = [start, end]; iterate page/page_size until
        page >= page_info.total_page. Resolve campaign_id -> campaign_name via /campaign/get/.
    Mapping: source key 'tiktok' in config/mappings.yaml (stat_time_day->date,
        total_complete_payment->platform_revenue, ...); channel='tiktok'; then to_canonical.
    """

    name = "tiktok"
    source = "tiktok"

    def __init__(self, advertiser_id: str | None = None):
        self.advertiser_id = advertiser_id

    def fetch(self, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        raise NotImplementedError(
            "TikTokSource is a Phase-2 skeleton. Authenticate via require_credentials("
            "'TIKTOK_ACCESS_TOKEN', 'TIKTOK_ADVERTISER_ID'), call "
            "/open_api/v1.3/report/integrated/get/ for [start, end] (page/page_size paging), "
            "then schema.to_canonical(df, 'tiktok', load_mappings())."
        )


class LinkedInSource(DataSource):
    """LinkedIn Marketing API connector (skeleton).

    Credentials (.env): LINKEDIN_ACCESS_TOKEN, LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET,
        LINKEDIN_AD_ACCOUNT_ID.
    Endpoint: GET /rest/adAnalytics?q=analytics with pivot=CAMPAIGN and fields
        [costInLocalCurrency, impressions, clicks, externalWebsiteConversions,
        conversionValueInLocalCurrency, dateRange].
    Date range / pagination: dateRange.(start|end).(year|month|day) = [start, end]; page via
        start/count. Resolve campaign URNs -> names via /rest/adCampaigns.
    Mapping: source key 'linkedin' in config/mappings.yaml (costInLocalCurrency->spend,
        externalWebsiteConversions->conversions, ...); channel='linkedin'; then to_canonical.
    """

    name = "linkedin"
    source = "linkedin"

    def __init__(self, ad_account_id: str | None = None):
        self.ad_account_id = ad_account_id

    def fetch(self, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        raise NotImplementedError(
            "LinkedInSource is a Phase-2 skeleton. Authenticate via require_credentials("
            "'LINKEDIN_ACCESS_TOKEN', 'LINKEDIN_AD_ACCOUNT_ID'), GET /rest/adAnalytics "
            "(q=analytics, pivot=CAMPAIGN, dateRange=[start, end], start/count paging), then "
            "schema.to_canonical(df, 'linkedin', load_mappings())."
        )


class GA4Source(DataSource):
    """Google Analytics 4 (GA4) connector (skeleton) — the mid-funnel / engagement source.

    GA4 has NO ad spend; it contributes engagement (sessions/page views) and MERGES onto the
    ad data on date x channel x campaign x geo. Its canonical rows leave the ad-metric columns
    null (NaN) and populate the optional mid-funnel columns.

    Credentials (.env): GA4_PROPERTY_ID (+ Google OAuth creds, reused from the Google stack).
    Endpoint: GA4 Data API ``properties.runReport`` (analyticsdata v1beta) on the property.
    Dimensions: date, sessionSource, sessionMedium, sessionCampaignName.
    Metrics: sessions, engagedSessions, screenPageViews (optionally userEngagementDuration ->
        avg_engagement_seconds).
    Date range / pagination: ``dateRanges=[{startDate:start,endDate:end}]``; page via
        ``limit``/``offset`` until rows are exhausted.
    Mapping: source key 'ga4' in config/mappings.yaml. GA4 keys on UTM, so derive canonical
        ``channel`` from sessionSource (+ sessionMedium) via channel_aliases and ``campaign``
        from sessionCampaignName; set ``geo`` from the property/region; then
        ``schema.to_canonical(df, 'ga4', load_mappings())`` and merge onto ad rows on the keys.
    """

    name = "ga4"
    source = "ga4"

    def __init__(self, property_id: str | None = None):
        self.property_id = property_id

    def fetch(self, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        raise NotImplementedError(
            "GA4Source is a Phase-2 skeleton. Authenticate via require_credentials("
            "'GA4_PROPERTY_ID'), call the GA4 Data API properties.runReport (dimensions "
            "date/sessionSource/sessionMedium/sessionCampaignName, metrics sessions/"
            "engagedSessions/screenPageViews) for [start, end], derive channel from "
            "source+medium, then schema.to_canonical(df, 'ga4', load_mappings()) and merge "
            "onto the ad data on date x channel x campaign x geo."
        )
