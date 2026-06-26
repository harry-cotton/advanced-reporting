"""Select a data source by name so the pipeline stays source-agnostic.

Mirrors ``mmm/factory.py``: ``synthetic`` is the active default; ``csv`` reads local
files; the rest are Phase-2 connector skeletons. Classes are lazy-imported so importing
this module never pulls heavy/optional connector deps.
"""
from __future__ import annotations
from .base import DataSource

_VALID = ("synthetic", "csv", "supermetrics", "google_ads", "meta", "tiktok",
          "linkedin", "ga4")


def get_source(name: str | None = None, **kwargs) -> DataSource:
    name = (name or "synthetic").lower()
    if name == "synthetic":
        from .synthetic import SyntheticSource
        return SyntheticSource(**kwargs)
    if name == "csv":
        from .csv_source import CSVSource
        return CSVSource(**kwargs)
    if name == "supermetrics":
        from .supermetrics import SupermetricsSource
        return SupermetricsSource(**kwargs)
    if name == "google_ads":
        from .connectors import GoogleAdsSource
        return GoogleAdsSource(**kwargs)
    if name == "meta":
        from .connectors import MetaSource
        return MetaSource(**kwargs)
    if name == "tiktok":
        from .connectors import TikTokSource
        return TikTokSource(**kwargs)
    if name == "linkedin":
        from .connectors import LinkedInSource
        return LinkedInSource(**kwargs)
    if name == "ga4":
        from .connectors import GA4Source
        return GA4Source(**kwargs)
    raise ValueError(f"Unknown data source '{name}'. Use one of: {', '.join(_VALID)}.")
