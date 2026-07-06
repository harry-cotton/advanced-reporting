"""Canonical ingestion schema for granular campaign data.

The canonical long schema is at DAILY grain: one row per
``date x channel x campaign x geo``. Every ``DataSource.fetch()`` is expected to
return (or be normalizable to) these columns, so the transform/modeling/reporting
layers never need to know which platform the data came from.

Columns
-------
- ``date``              : reporting day (datetime).
- ``channel``           : canonical channel key (e.g. ``meta``, ``google_search``).
- ``campaign``          : campaign name/id within the channel.
- ``geo``               : geography for the row (default ``"national"``).
- ``spend``             : media cost in ``currency``.
- ``impressions``       : ad impressions.
- ``clicks``            : ad clicks.
- ``conversions``       : attributed conversions.
- ``platform_revenue``  : platform-ATTRIBUTED conversion value/revenue.
- ``currency``          : ISO currency code for monetary fields (default ``"USD"`` for
                          trusted-default sources only; real sources must provide it).
- ``campaign_id``       : platform campaign id (optional; names get renamed, ids don't).
- ``account_id``        : ad-account id (optional; disambiguates same-named campaigns).
- ``ad_group``          : platform sub-campaign entity name (Meta ad set, Google ad
                          group, LinkedIn creative). ``""`` = campaign-level row. Part of
                          the dedup grain; when both grains arrive for the same campaign,
                          the store keeps the ad-level rows and drops their campaign-level
                          aggregate (see ``store.consolidate``).

Decoded naming-convention fields (OPTIONAL, ``""`` for campaign-level sources; parsed
from ``ad_group`` by ``ingestion/naming_decode.py`` using the naming generator's
grammar — names that don't conform land in ``audience_type="(unparsed)"``, never guessed):
- ``audience_type``     : e.g. ``PROSPECT`` / ``RETARGET`` (or ``"(unparsed)"``).
- ``audience_detail``   : e.g. ``LAL-1PCT`` / ``SITE-90D``.
- ``creative``          : creative concept, from Ad-grammar names (e.g. ``BRANDHERO``).
- ``creative_format``   : e.g. ``VID`` / ``STATIC``.
- ``source``            : which extractor produced the row (stamped by the store). Part
                          of the dedup grain so ad rows and web-analytics rows for the
                          same (date, channel, campaign, geo) coexist and merge
                          column-wise downstream instead of overwriting each other.

Mid-funnel / web-analytics columns (OPTIONAL, nullable -- ad platforms don't measure
them, GA4/analytics do; default NaN = "not measured", NOT 0):
- ``sessions``                : landing sessions (GA4 ``sessions``).
- ``engaged_sessions``        : engaged sessions (GA4 ``engagedSessions``).
- ``key_events``              : analytics-measured conversions (GA4 key events, e.g.
                                start_application). Deliberately SEPARATE from the ad
                                platforms' self-attributed ``conversions``.
- ``page_views``              : page/screen views (GA4 ``screenPageViews``).
- ``video_views``             : video views (video-ish channels).
- ``avg_engagement_seconds``  : average engagement time per session.

Naming note: ``platform_revenue`` is the revenue the ad PLATFORM attributes to its
own conversions. It is intentionally DISTINCT from the business KPI ``revenue`` (the
MMM target in ``business_kpi_weekly.csv``), which is not part of this per-channel ad
schema. Real connectors map their conversion-value field (Google Ads "Conv. value",
Meta ``action_values``, etc.) onto ``platform_revenue`` -- see ``config/mappings.yaml``.

``geo``/``currency`` and the mid-funnel columns are OPTIONAL: ``normalize()`` fills them
with defaults when a source omits them, so every existing ad source still validates.
"""
from __future__ import annotations
import hashlib
from dataclasses import dataclass

import pandas as pd

# Bump on any canonical column change (human-readable label). The hash in
# ``schema_signature()`` also changes automatically, so a forgotten bump can't cause a
# silent schema mismatch. v2 = engagement tier; v3 = source/campaign_id/account_id
# (grain hardening for real multi-source data, 2026-07); v4 = key_events (analytics-
# measured conversions, distinct from platform-claimed `conversions` so weekly sums
# never add the two attribution systems together); v5 = ad-level grain (`ad_group` in
# the dedup key + audience/creative fields decoded from the naming convention).
SCHEMA_VERSION = 5


class SchemaError(ValueError):
    """Raised when a dataframe violates the canonical schema."""


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    dtype: str          # "datetime64[ns]" | "string" | "float64"
    required: bool
    default: object = None  # used by normalize() only when required is False


# Single source of truth: order, dtype, required-ness and defaults of every column.
# Mid-funnel engagement columns are OPTIONAL with a NaN default ("not measured"), so
# ad-platform sources still validate while GA4/analytics populate them.
_NA = float("nan")
CANONICAL_SCHEMA: tuple[ColumnSpec, ...] = (
    ColumnSpec("date", "datetime64[ns]", True),
    ColumnSpec("channel", "string", True),
    ColumnSpec("campaign", "string", True),
    ColumnSpec("campaign_id", "string", False, ""),
    ColumnSpec("account_id", "string", False, ""),
    ColumnSpec("ad_group", "string", False, ""),
    ColumnSpec("audience_type", "string", False, ""),
    ColumnSpec("audience_detail", "string", False, ""),
    ColumnSpec("creative", "string", False, ""),
    ColumnSpec("creative_format", "string", False, ""),
    ColumnSpec("geo", "string", False, "national"),
    ColumnSpec("spend", "float64", True),
    ColumnSpec("impressions", "float64", True),
    ColumnSpec("clicks", "float64", True),
    ColumnSpec("conversions", "float64", True),
    ColumnSpec("platform_revenue", "float64", True),
    ColumnSpec("currency", "string", False, "USD"),
    ColumnSpec("source", "string", False, ""),
    ColumnSpec("sessions", "float64", False, _NA),
    ColumnSpec("engaged_sessions", "float64", False, _NA),
    ColumnSpec("key_events", "float64", False, _NA),
    ColumnSpec("page_views", "float64", False, _NA),
    ColumnSpec("video_views", "float64", False, _NA),
    ColumnSpec("avg_engagement_seconds", "float64", False, _NA),
)

CANONICAL_COLUMNS = tuple(c.name for c in CANONICAL_SCHEMA)
REQUIRED_COLUMNS = tuple(c.name for c in CANONICAL_SCHEMA if c.required)
OPTIONAL_COLUMNS = tuple(c.name for c in CANONICAL_SCHEMA if not c.required)
METRIC_COLUMNS = ("spend", "impressions", "clicks", "conversions", "platform_revenue")

_SPEC_BY_NAME = {c.name: c for c in CANONICAL_SCHEMA}


def schema_signature() -> str:
    """Stable signature of the current canonical schema: ``"v{VERSION}:{hash}"``.

    The hash is a digest of the canonical column set, so it changes automatically whenever
    columns are added/removed/renamed. The store stamps each pull with this and refuses to
    merge pulls whose signature differs (see ``store.consolidate``).
    """
    h = hashlib.sha1(",".join(CANONICAL_COLUMNS).encode("utf-8")).hexdigest()[:12]
    return f"v{SCHEMA_VERSION}:{h}"


def apply_source_map(df: pd.DataFrame, source: str, mappings: dict) -> pd.DataFrame:
    """Rename a raw source frame's columns to canonical names.

    Uses ``mappings["sources"][source]`` (a ``{raw_col: canonical_col}`` dict). If
    ``source`` is unknown, falls back to the ``"default"`` identity map. Only renames
    columns that are present; never drops unmapped columns. Returns a copy.
    """
    sources = (mappings or {}).get("sources", {})
    colmap = sources.get(source)
    if colmap is None:
        colmap = sources.get("default", {})
    rename = {raw: canon for raw, canon in colmap.items() if raw in df.columns}
    return df.rename(columns=rename).copy()


def normalize(df: pd.DataFrame, *, currency: str | None = None,
              coerce_dtypes: bool = False) -> pd.DataFrame:
    """Fill optional columns with defaults and order columns canonically.

    - Adds any absent OPTIONAL column with its default (``geo="national"``;
      ``currency`` = the ``currency`` arg if given, else the spec default ``"USD"``).
    - Does NOT add or fill REQUIRED columns -- their absence is surfaced by ``validate``.
    - Reorders to canonical order; any extra (non-canonical) columns are appended so
      nothing is silently dropped.
    - ``coerce_dtypes`` is False by default so ingestion stays a pure structural
      normalize (no numeric coercion that could change downstream cleaning). When True,
      each present column is coerced to its spec dtype.
    """
    df = df.copy()
    for spec in CANONICAL_SCHEMA:
        if spec.required or spec.name in df.columns:
            continue
        df[spec.name] = currency if (spec.name == "currency" and currency is not None) \
            else spec.default

    if coerce_dtypes:
        for name in df.columns:
            spec = _SPEC_BY_NAME.get(name)
            if spec is None:
                continue
            if spec.dtype == "datetime64[ns]":
                df[name] = pd.to_datetime(df[name], errors="coerce")
            elif spec.dtype == "float64":
                df[name] = pd.to_numeric(df[name], errors="coerce").astype("float64")
            elif spec.dtype == "string":
                df[name] = df[name].astype("string")

    ordered = [c for c in CANONICAL_COLUMNS if c in df.columns]
    extra = [c for c in df.columns if c not in CANONICAL_COLUMNS]
    return df[ordered + extra]


def validate(df: pd.DataFrame, *, require_optional: bool = False) -> pd.DataFrame:
    """Return ``df`` unchanged if it has all required columns, else raise SchemaError."""
    needed = set(REQUIRED_COLUMNS)
    if require_optional:
        needed |= set(OPTIONAL_COLUMNS)
    missing = needed - set(df.columns)
    if missing:
        raise SchemaError(f"Missing required columns: {sorted(missing)}")
    return df


# Sources whose currency default is trusted: the identity map (already-canonical pulls
# re-read by the store) and the synthetic DGP. Every REAL source must carry a currency —
# silently stamping USD on a EUR account is consistent-but-wrong, the worst failure mode.
_CURRENCY_DEFAULT_OK = {"default", "synthetic"}


def _web_analytics_sources(mappings: dict) -> set[str]:
    return set((mappings or {}).get("web_analytics_sources") or ())


def to_canonical(df: pd.DataFrame, source: str, mappings: dict, *,
                 currency: str | None = None) -> pd.DataFrame:
    """One-call ingestion normalizer a connector uses: map -> normalize -> validate.

    Web-analytics sources (``mappings["web_analytics_sources"]``, e.g. GA4) measure no
    ad metrics: their required ad columns are filled with NaN ("not measured") and
    their currency is left null — following the documented GA4 path used to raise
    SchemaError, and hand-fixing it would have let the store's old keep-last dedup
    silently destroy either the ad rows or the analytics rows.
    """
    df = apply_source_map(df, source, mappings)
    is_web = source in _web_analytics_sources(mappings)
    if is_web:
        df = df.copy()
        for col in METRIC_COLUMNS:
            if col not in df.columns:
                df[col] = float("nan")
        if "currency" not in df.columns:
            df["currency"] = pd.NA        # no monetary fields -> no currency claim
    elif (source not in _CURRENCY_DEFAULT_OK and currency is None
          and "currency" not in df.columns):
        raise SchemaError(
            f"source '{source}' provides no currency — map a currency column or pass "
            "currency= explicitly (refusing to silently assume USD)")
    df = normalize(df, currency=currency)
    validate(df)
    return df
