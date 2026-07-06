"""Readers for manually-downloaded platform export files (the ``data/inbox/`` flow).

Each reader turns one real-world export format — quirks and all — into canonical rows
ready for the durable store. ``read_export(path)`` sniffs the format and dispatches;
unknown formats raise loudly (never guess at someone's data).

Formats handled (fixtures: ``scripts/generate_sample_exports.py``):
- Google Ads campaign report: title rows before the header, quoted thousands
  separators, fractional conversions, ``Total:`` summary rows.
- Meta Ads Manager export: verbose headers ("Amount spent (USD)", "Link clicks").
- LinkedIn Campaign Manager export: preamble lines (account + currency live there,
  not in columns), M/D/YYYY dates.
- GA4 traffic acquisition export: ``#`` comment preamble, YYYYMMDD dates,
  source/medium -> channel resolution (campaign-name overrides first, so
  'google / cpc' can split into search vs demand gen), organic/direct rows kept as
  their own non-ad channels, Key events -> ``key_events`` (NEVER ``conversions`` —
  platform-claimed and analytics-measured conversions are different numbers and
  must not be summed together).
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from . import schema
from ..utils import load_mappings

# source names the store will file pulls under
GOOGLE, META, LINKEDIN, GA4 = "google_ads", "meta_ads", "linkedin_ads", "ga4"


def _num(s) -> pd.Series:
    """Parse export numerics: strip quotes/commas/whitespace -> float ('1,204' -> 1204)."""
    return pd.to_numeric(
        pd.Series(s).astype(str).str.replace(",", "", regex=False).str.strip()
        .replace({"": None, "--": None}),
        errors="coerce")


def detect_format(path) -> str | None:
    """Sniff which platform an export file came from (None = unrecognized)."""
    head = Path(path).read_text(encoding="utf-8-sig", errors="replace")[:2000]
    first = head.splitlines()[0].strip() if head.splitlines() else ""
    if first.startswith("Campaign report"):
        return GOOGLE
    if first.startswith("Campaign Performance Report"):
        return LINKEDIN
    if first.startswith("#") and "Session source / medium" in head:
        return GA4
    if "Amount spent" in first:
        return META
    return None


def _find_header_row(path: Path, marker: str) -> int:
    """Line index of the real CSV header (the line starting with ``marker``)."""
    for i, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines()):
        if line.split(",")[0].strip().strip('"') == marker:
            return i
    raise schema.SchemaError(f"{path.name}: no header row starting with '{marker}' found")


def read_google_ads_export(path, mappings=None) -> pd.DataFrame:
    path = Path(path)
    mappings = mappings or load_mappings()
    hdr = _find_header_row(path, "Day")
    df = pd.read_csv(path, skiprows=hdr)
    df = df[~df["Day"].astype(str).str.startswith("Total")]      # summary rows out
    ctype = df["Campaign type"].astype(str).str.strip().str.lower()
    out = pd.DataFrame({
        "date": pd.to_datetime(df["Day"], errors="coerce"),
        "channel": ctype.map(lambda t: "google_search" if t == "search"
                             else "google_demandgen"),
        "campaign": df["Campaign"].astype(str).str.strip(),
        "campaign_id": df["Campaign ID"].astype(str).str.strip(),
        "spend": _num(df["Cost"]),
        "impressions": _num(df["Impr."]),
        "clicks": _num(df["Clicks"]),
        "conversions": _num(df["Conversions"]),
        "platform_revenue": _num(df["Conv. value"]),
        "currency": df["Currency code"].astype(str).str.strip(),
    })
    return schema.to_canonical(out, GOOGLE, mappings)


def read_meta_export(path, mappings=None) -> pd.DataFrame:
    path = Path(path)
    mappings = mappings or load_mappings()
    df = pd.read_csv(path)
    spend_col = next((c for c in df.columns if c.startswith("Amount spent")), None)
    if spend_col is None:
        raise schema.SchemaError(f"{path.name}: no 'Amount spent' column")
    m = re.search(r"\(([A-Z]{3})\)", spend_col)                  # currency in the header
    currency = m.group(1) if m else None
    out = pd.DataFrame({
        "date": pd.to_datetime(df["Day"], errors="coerce"),
        "channel": "meta",
        "campaign": df["Campaign name"].astype(str).str.strip(),
        "spend": _num(df[spend_col]),
        "impressions": _num(df["Impressions"]),
        "clicks": _num(df["Link clicks"]),
        "conversions": _num(df["Results"]),
        "platform_revenue": float("nan"),                        # not in this export
    })
    return schema.to_canonical(out, META, mappings, currency=currency)


def read_linkedin_export(path, mappings=None) -> pd.DataFrame:
    path = Path(path)
    mappings = mappings or load_mappings()
    text = path.read_text(encoding="utf-8-sig")
    currency = account = None
    for line in text.splitlines()[:8]:                            # preamble metadata
        if line.startswith("Currency:"):
            currency = line.split(":", 1)[1].strip()
        m = re.search(r"Account:.*\((\d+)\)", line)
        if m:
            account = m.group(1)
    hdr = _find_header_row(path, "Start Date (in UTC)")
    df = pd.read_csv(path, skiprows=hdr)
    out = pd.DataFrame({
        "date": pd.to_datetime(df["Start Date (in UTC)"], format="%m/%d/%Y",
                               errors="coerce"),                  # locale dates, explicit
        "channel": "linkedin",
        "campaign": df["Campaign Name"].astype(str).str.strip(),
        "campaign_id": df["Campaign ID"].astype(str).str.strip(),
        "account_id": account or "",
        "spend": _num(df["Total Spent"]),
        "impressions": _num(df["Impressions"]),
        "clicks": _num(df["Clicks"]),
        "conversions": _num(df["Conversions"]),
        "platform_revenue": float("nan"),
    })
    return schema.to_canonical(out, LINKEDIN, mappings, currency=currency)


def _ga4_channel(source_medium: pd.Series, campaign: pd.Series, mappings: dict) -> pd.Series:
    cfg = (mappings or {}).get("ga4_export", {}) or {}
    overrides = cfg.get("campaign_channel_overrides") or {}
    sm_map = {str(k).strip(): v for k, v in (cfg.get("source_medium_channels") or {}).items()}

    def resolve(sm: str, camp: str) -> str:
        for pat, ch in overrides.items():                        # campaign name wins
            if pat.lower() in str(camp).lower():
                return ch
        return sm_map.get(str(sm).strip(), f"unmapped:{str(sm).strip()}")

    return pd.Series([resolve(sm, c) for sm, c in zip(source_medium, campaign)])


def read_ga4_export(path, mappings=None) -> pd.DataFrame:
    path = Path(path)
    mappings = mappings or load_mappings()
    df = pd.read_csv(path, comment="#")
    channel = _ga4_channel(df["Session source / medium"], df["Session campaign"], mappings)
    unmapped = sorted(set(channel[channel.str.startswith("unmapped:")]))
    if unmapped:
        raise schema.SchemaError(
            f"{path.name}: unmapped GA4 source/medium value(s) "
            f"{[u.split(':', 1)[1] for u in unmapped]} — add them to "
            "config/mappings.yaml ga4_export.source_medium_channels")
    out = pd.DataFrame({
        "date": pd.to_datetime(df["Date"].astype(str), format="%Y%m%d", errors="coerce"),
        "channel": channel,
        "campaign": df["Session campaign"].astype(str).str.strip(),
        "sessions": _num(df["Sessions"]),
        "engaged_sessions": _num(df["Engaged sessions"]),
        "key_events": _num(df["Key events"]),
    })
    return schema.to_canonical(out, GA4, mappings)


_READERS = {GOOGLE: read_google_ads_export, META: read_meta_export,
            LINKEDIN: read_linkedin_export, GA4: read_ga4_export}


def read_export(path, mappings=None) -> tuple[str, pd.DataFrame]:
    """Sniff + parse one export file -> ``(source_name, canonical_df)``.

    Raises SchemaError on an unrecognized format — a file nobody can identify must
    never be silently guessed into the store.
    """
    fmt = detect_format(path)
    if fmt is None:
        raise schema.SchemaError(
            f"{Path(path).name}: unrecognized export format — expected a Google Ads / "
            "Meta / LinkedIn / GA4 export (see data/inbox/README.md)")
    return fmt, _READERS[fmt](path, mappings)
