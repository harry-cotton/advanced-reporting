"""Decode naming-convention names back into plan fields (the naming generator's inverse).

The generator (``naming/naming_generator.py``) composes names from ordered fields with a
fixed grammar (its ``DEFAULT_SCHEME``), skipping empty fields:

    Ad Set = audience_type _ audience_detail _ placement     e.g. PROSPECT_LAL-1PCT_FEED
    Ad     = creative _ format _ size _ version              e.g. BRANDHERO_VID_9x16_V1
             (an Ad without a version is 3 tokens: BRANDHERO_VID_9x16)

Decoding is structural (token count + the generator's legal-character rule) plus two
small vocabularies (creative formats; size/version shapes) to tell a 3-token Ad from a
3-token Ad Set. Names that don't match the grammar land in an explicit
``audience_type="(unparsed)"`` bucket — NEVER guessed. The unparsed RATE is itself a
reported metric (and the sales pitch for adopting the naming convention). A blank name
(campaign-level row) decodes to all-empty fields, not "(unparsed)".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

UNPARSED = "(unparsed)"

# One naming-grammar token (the generator's NAME_RE allows A-Za-z0-9_- for a whole
# name; '_' is the delimiter, so an individual token is A-Za-z0-9- and non-empty).
_TOKEN_RE = re.compile(r"^[A-Za-z0-9\-]+$")
_SIZE_RE = re.compile(r"^\d+x\d+$")          # 1x1, 4x5, 9x16, 1080x1080
_VERSION_RE = re.compile(r"^[Vv]\d+$")       # V1, V2, ...
# Creative-format vocab (config/planner_rails.yaml creatives + common export values).
_FORMATS = {"VID", "VIDEO", "STATIC", "IMG", "IMAGE", "CAROUSEL", "DOC", "GIF", "UGC"}

# The decoded columns carried on the canonical schema (ingestion/schema.py v5).
FIELD_COLUMNS = ("audience_type", "audience_detail", "creative", "creative_format")

_EMPTY = dict.fromkeys(FIELD_COLUMNS, "")


@dataclass(frozen=True)
class DecodedName:
    kind: str                    # "ad_set" | "ad" | "unparsed" | "blank"
    audience_type: str = ""
    audience_detail: str = ""
    placement: str = ""
    creative: str = ""
    creative_format: str = ""


# Campaign grammar (generator DEFAULT_SCHEME "Campaign"):
#   market _ channel _ objective _ audience_type [_ initiative]
# The first four segments are the classic form; ``initiative`` is an OPTIONAL trailing
# segment (a career path / product line, e.g. SA). Reading segments 0-3 is unchanged, so
# every pre-initiative 4-segment name still decodes — the 5th segment is purely additive.
_CAMPAIGN_FIELDS = ("market", "channel", "objective", "audience_type", "initiative")


@dataclass(frozen=True)
class DecodedCampaign:
    kind: str                    # "campaign" | "unparsed" | "blank"
    market: str = ""
    channel: str = ""
    objective: str = ""
    audience_type: str = ""
    initiative: str = ""         # "" when the name is the classic 4-segment form


def decode_name(name) -> DecodedName:
    """Parse one ad-set/ad name against the naming grammar. Deterministic, never guesses."""
    text = "" if name is None or (isinstance(name, float) and pd.isna(name)) else str(name).strip()
    if not text:
        return DecodedName("blank")
    tokens = text.split("_")
    if 2 <= len(tokens) <= 4 and all(_TOKEN_RE.match(t) for t in tokens):
        # Ad-grammar signals: a trailing version, a size token, or a known format token.
        is_ad = (_VERSION_RE.match(tokens[-1]) is not None
                 or any(_SIZE_RE.match(t) for t in tokens[1:])
                 or tokens[1].upper() in _FORMATS)
        if is_ad:
            fmt = tokens[1] if tokens[1].upper() in _FORMATS else ""
            return DecodedName("ad", creative=tokens[0], creative_format=fmt)
        # audience_type _ audience_detail [_ placement] — grammar tokens are UPPERCASE
        # (the generator uppercases its vocab), so a lowercase 2-token legacy name like
        # "cyber_evergreen" must land in (unparsed), not decode into a fake audience.
        if len(tokens) <= 3 and all(t == t.upper() for t in tokens):
            return DecodedName("ad_set", audience_type=tokens[0],
                               audience_detail=tokens[1],
                               placement=tokens[2] if len(tokens) == 3 else "")
    return DecodedName("unparsed", audience_type=UNPARSED)


def decode_campaign_name(name) -> DecodedCampaign:
    """Parse one CAMPAIGN name against the campaign grammar. Deterministic, never guesses.

    4 tokens -> the classic form (``initiative`` empty); 5 tokens -> the 5th is the
    trailing career-path ``initiative``. A blank name is a naming failure at campaign
    grain only if it should have had a name — here it decodes to ``blank`` (all empty),
    mirroring ``decode_name``. Anything else (wrong token count, illegal characters)
    lands in ``unparsed``, never guessed.
    """
    text = "" if name is None or (isinstance(name, float) and pd.isna(name)) else str(name).strip()
    if not text:
        return DecodedCampaign("blank")
    tokens = text.split("_")
    if len(tokens) in (4, 5) and all(_TOKEN_RE.match(t) for t in tokens):
        return DecodedCampaign("campaign", market=tokens[0], channel=tokens[1],
                               objective=tokens[2], audience_type=tokens[3],
                               initiative=tokens[4] if len(tokens) == 5 else "")
    return DecodedCampaign("unparsed")


def decode_initiative(name) -> str:
    """The trailing career-path ``initiative`` for a campaign name ("" if absent/unparsed)."""
    return decode_campaign_name(name).initiative


def norm_key(name) -> str:
    """Normalize a name for crosswalk matching: lowercase, collapse whitespace."""
    return re.sub(r"\s+", " ", str(name).strip().lower())


def decode_fields(name, overrides: dict | None = None) -> dict:
    """The four canonical-schema columns decoded from one name (see FIELD_COLUMNS).

    A curated ``overrides`` crosswalk (raw name -> fields, keyed by ``norm_key``) is
    consulted FIRST — the analyst's explicit remediation for names that don't follow the
    convention. Only then the grammar, then ``(unparsed)``. This is human-decided
    mapping, never a guess.
    """
    if overrides:
        ov = overrides.get(norm_key(name))
        if ov:
            return {c: str(ov.get(c, "") or "") for c in FIELD_COLUMNS}
    d = decode_name(name)
    return {"audience_type": d.audience_type, "audience_detail": d.audience_detail,
            "creative": d.creative, "creative_format": d.creative_format}


def decode_series(names: pd.Series, overrides: dict | None = None) -> pd.DataFrame:
    """Decode a series of ad_group names -> DataFrame of FIELD_COLUMNS (index-aligned).

    Names repeat daily in exports, so decoding is memoized over uniques. ``overrides`` is
    the curated crosswalk (see ``decode_fields``).
    """
    names = pd.Series(names)
    memo = {n: decode_fields(n, overrides) for n in pd.unique(names.dropna())}
    return pd.DataFrame([memo.get(n, dict(_EMPTY)) for n in names], index=names.index)


def unparsed_rate(df: pd.DataFrame) -> float:
    """Share of AD-LEVEL rows (ad_group != "") whose name didn't parse.

    Campaign-level rows are excluded from the denominator; ad rows that parsed under the
    Ad grammar (creative fields, empty audience fields) count as parsed.
    """
    if "ad_group" not in df.columns or "audience_type" not in df.columns:
        return 0.0
    ad_level = df[df["ad_group"].fillna("").astype(str).str.strip() != ""]
    if ad_level.empty:
        return 0.0
    return float((ad_level["audience_type"] == UNPARSED).mean())
