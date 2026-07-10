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

Ad-level variants (same platforms, one grain finer — ``ad_group`` carries the ad-set /
ad-group / creative name, and the naming-convention fields are decoded at ingest via
``naming_decode`` so the store carries them):
- Google Ads ad group report, Meta ad-set export, LinkedIn creative report.
Ad-level pulls file under the SAME source as their campaign-level sibling; when both
grains land for one campaign the store keeps the ad-level rows and drops the
campaign-level aggregate (``store.consolidate``), so mixed drops never double-count.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from . import naming_decode, schema
from ..utils import load_mappings, load_naming_overrides

# source names the store will file pulls under
GOOGLE, META, LINKEDIN, GA4 = "google_ads", "meta_ads", "linkedin_ads", "ga4"
# ad-level format keys (distinct from the source: read_export returns the SOURCE, so
# e.g. a Meta ad-set export still files under `meta_ads` next to campaign-level pulls)
GOOGLE_ADGROUPS, META_ADSETS, LINKEDIN_CREATIVES = (
    "google_ads_adgroups", "meta_ads_adsets", "linkedin_ads_creatives")


def _with_decoded(out: pd.DataFrame, names: pd.Series) -> pd.DataFrame:
    """Attach ad_group + the decoded naming-convention fields to a canonical frame."""
    out = out.copy()
    out["ad_group"] = names.astype(str).str.strip().to_numpy()
    decoded = naming_decode.decode_series(out["ad_group"], overrides=load_naming_overrides())
    for col in naming_decode.FIELD_COLUMNS:
        out[col] = decoded[col].to_numpy()
    return out


def _num(s) -> pd.Series:
    """Parse export numerics: strip quotes/commas/whitespace -> float ('1,204' -> 1204)."""
    return pd.to_numeric(
        pd.Series(s).astype(str).str.replace(",", "", regex=False).str.strip()
        .replace({"": None, "--": None}),
        errors="coerce")


def detect_format(path) -> str | None:
    """Sniff which platform/grain an export file came from (None = unrecognized)."""
    head = Path(path).read_text(encoding="utf-8-sig", errors="replace")[:2000]
    first = head.splitlines()[0].strip() if head.splitlines() else ""
    if first.startswith("Campaign report"):
        return GOOGLE
    if first.startswith("Ad group report"):
        return GOOGLE_ADGROUPS
    if first.startswith("Creative Performance Report"):
        return LINKEDIN_CREATIVES
    if first.startswith("Campaign Performance Report"):
        return LINKEDIN
    if first.startswith("#") and "Session source / medium" in head:
        return GA4
    if "Amount spent" in first:                       # ad-set check first: both match
        return META_ADSETS if "Ad set name" in first else META
    return None


def _find_header_row(path: Path, marker: str) -> int:
    """Line index of the real CSV header (the line starting with ``marker``)."""
    for i, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines()):
        if line.split(",")[0].strip().strip('"') == marker:
            return i
    raise schema.SchemaError(f"{path.name}: no header row starting with '{marker}' found")


def _google_frame(path: Path) -> pd.DataFrame:
    """Shared Google Ads UI-report parse: skip title rows, drop totals, map channels."""
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
    if "Video views" in df.columns:                      # Demand Gen / video (mid-funnel)
        out["video_views"] = _num(df["Video views"])
    if "Ad group" in df.columns:
        out = _with_decoded(out, df["Ad group"])
    return out


def read_google_ads_export(path, mappings=None) -> pd.DataFrame:
    return schema.to_canonical(_google_frame(Path(path)), GOOGLE,
                               mappings or load_mappings())


def read_google_adgroup_export(path, mappings=None) -> pd.DataFrame:
    """Google Ads *ad group* report: campaign-report quirks + an ``Ad group`` column."""
    path = Path(path)
    out = _google_frame(path)
    if "ad_group" not in out.columns:
        raise schema.SchemaError(f"{path.name}: ad group report without an 'Ad group' column")
    return schema.to_canonical(out, GOOGLE, mappings or load_mappings())


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
    if "Video plays" in df.columns:                              # mid-funnel engagement
        out["video_views"] = _num(df["Video plays"])
    if "Ad set name" in df.columns:                              # ad-set-level export
        out = _with_decoded(out, df["Ad set name"])
    return schema.to_canonical(out, META, mappings, currency=currency)


def _linkedin_frame(path: Path) -> tuple[pd.DataFrame, str | None]:
    """Shared LinkedIn Campaign Manager parse: preamble metadata + locale dates."""
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
    if "Video Views" in df.columns:                              # mid-funnel engagement
        out["video_views"] = _num(df["Video Views"])
    # LinkedIn's hierarchy is Campaign -> Creative (no ad-set tier); a creative report's
    # Creative Name is the sub-campaign entity, so it lands in ad_group like the others.
    if "Creative Name" in df.columns:
        out = _with_decoded(out, df["Creative Name"])
    return out, currency


def read_linkedin_export(path, mappings=None) -> pd.DataFrame:
    path = Path(path)
    out, currency = _linkedin_frame(path)
    return schema.to_canonical(out, LINKEDIN, mappings or load_mappings(),
                               currency=currency)


def read_linkedin_creative_export(path, mappings=None) -> pd.DataFrame:
    """LinkedIn *creative* performance report: campaign-report quirks + Creative Name."""
    path = Path(path)
    out, currency = _linkedin_frame(path)
    if "ad_group" not in out.columns:
        raise schema.SchemaError(
            f"{path.name}: creative report without a 'Creative Name' column")
    return schema.to_canonical(out, LINKEDIN, mappings or load_mappings(),
                               currency=currency)


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
    if "Views" in df.columns:                                    # GA4 page/screen views
        out["page_views"] = _num(df["Views"])
    return schema.to_canonical(out, GA4, mappings)


# format key -> (store source name, reader). Ad-level formats share their platform's
# source so campaign- and ad-level pulls meet in one folder and the store's supersede
# step can reconcile the two grains.
_READERS = {
    GOOGLE: (GOOGLE, read_google_ads_export),
    GOOGLE_ADGROUPS: (GOOGLE, read_google_adgroup_export),
    META: (META, read_meta_export),
    META_ADSETS: (META, read_meta_export),
    LINKEDIN: (LINKEDIN, read_linkedin_export),
    LINKEDIN_CREATIVES: (LINKEDIN, read_linkedin_creative_export),
    GA4: (GA4, read_ga4_export),
}


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
    source, reader = _READERS[fmt]
    return source, reader(path, mappings)
