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
- ``currency``          : ISO currency code for monetary fields (default ``"USD"``).

Mid-funnel / web-analytics columns (OPTIONAL, nullable -- ad platforms don't measure
them, GA4/analytics do; default NaN = "not measured", NOT 0):
- ``sessions``                : landing sessions (GA4 ``sessions``).
- ``engaged_sessions``        : engaged sessions (GA4 ``engagedSessions``).
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
# silent schema mismatch. The engagement / mid-funnel tier is v2.
SCHEMA_VERSION = 2


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
    ColumnSpec("geo", "string", False, "national"),
    ColumnSpec("spend", "float64", True),
    ColumnSpec("impressions", "float64", True),
    ColumnSpec("clicks", "float64", True),
    ColumnSpec("conversions", "float64", True),
    ColumnSpec("platform_revenue", "float64", True),
    ColumnSpec("currency", "string", False, "USD"),
    ColumnSpec("sessions", "float64", False, _NA),
    ColumnSpec("engaged_sessions", "float64", False, _NA),
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


def to_canonical(df: pd.DataFrame, source: str, mappings: dict, *,
                 currency: str | None = None) -> pd.DataFrame:
    """One-call ingestion normalizer a connector uses: map -> normalize -> validate."""
    df = apply_source_map(df, source, mappings)
    df = normalize(df, currency=currency)
    validate(df)
    return df
