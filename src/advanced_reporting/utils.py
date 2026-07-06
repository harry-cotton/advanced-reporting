"""Small shared helpers: project paths, config loading, and token-safe text matching."""
from __future__ import annotations
import os
import re
from pathlib import Path
import yaml


def norm_text(s) -> str:
    """Lowercase and collapse all non-alphanumerics to single spaces ('Tik-Tok_US' ->
    'tik tok us'), so phrase matching is separator-agnostic."""
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def standardize_channels(series, aliases: dict):
    """Lowercase/strip a channel Series and map through the alias table ('META',
    'facebook' -> 'meta'). Idempotent — safe to apply in the store AND in clean."""
    s = series.astype(str).str.strip().str.lower()
    return s.map(lambda v: (aliases or {}).get(v, v))


def phrase_in(text: str, phrase) -> bool:
    """Whole-word/phrase match: 'search' in 'paid search report' but NOT in 'research'.

    Both sides are normalized, so 'google_search' matches 'google search' and vice
    versa. This is the antidote to the raw-substring matching that made any text
    containing 'campaign' match the alias 'ig' (campa-IG-n)."""
    p = norm_text(phrase)
    return bool(p) and re.search(rf"\b{re.escape(p)}\b", norm_text(text)) is not None

# Canonical ad columns, kept here so the built-in mappings fallback has no import
# dependency on the ingestion package (avoids any import cycle).
_CANONICAL_AD_COLS = (
    "date", "channel", "campaign", "spend",
    "impressions", "clicks", "conversions", "platform_revenue",
)

# Used only when config/mappings.yaml is missing, so clean.py / ingestion never hard-fail.
# channel_aliases must stay in sync with transform/clean.py's fallback literal.
_DEFAULT_MAPPINGS = {
    "channel_aliases": {
        "facebook": "meta", "fb": "meta", "instagram": "meta", "ig": "meta",
        "google": "google_search", "search": "google_search", "google_search": "google_search",
        "pmax": "google_pmax", "performance_max": "google_pmax", "google_pmax": "google_pmax",
        "tik_tok": "tiktok", "tik-tok": "tiktok", "tiktok": "tiktok",
        "linked_in": "linkedin", "linkedin": "linkedin", "meta": "meta",
    },
    "sources": {"default": {c: c for c in _CANONICAL_AD_COLS}},
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_config(path: str | Path | None = None) -> dict:
    """Load config.yaml if present, else fall back to config.example.yaml."""
    root = project_root()
    if path is None:
        path = root / "config" / "config.yaml"
        if not Path(path).exists():
            path = root / "config" / "config.example.yaml"
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_env_file(path: str | Path | None = None) -> None:
    """Load a ``.env`` file into ``os.environ`` (dependency-free, no python-dotenv).

    Parses ``KEY=VALUE`` lines from ``<project_root>/.env`` if present, skipping blanks
    and ``#`` comments and stripping surrounding quotes. Existing environment variables
    are NOT overwritten (the real environment wins over the file). Missing file is a
    no-op, so this is always safe to call before reading credentials.
    """
    p = Path(path) if path is not None else project_root() / ".env"
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def load_mappings(path: str | Path | None = None) -> dict:
    """Load config/mappings.yaml (channel aliases + per-source column maps).

    Falls back to built-in defaults if the file is absent, and backfills any
    missing top-level section, so callers always get both keys present.
    """
    root = project_root()
    if path is None:
        path = root / "config" / "mappings.yaml"
    p = Path(path)
    if not p.exists():
        return _DEFAULT_MAPPINGS
    with open(p, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    # pass EVERYTHING through (new top-level sections must not be silently dropped),
    # backfilling the two core sections from the built-in defaults if absent
    out = dict(data)
    out.setdefault("channel_aliases", _DEFAULT_MAPPINGS["channel_aliases"])
    out.setdefault("sources", _DEFAULT_MAPPINGS["sources"])
    return out
