"""Durable, accumulating raw store for extracted campaign data.

Extraction (``scripts/ingest.py``) writes each pull as an immutable, date-stamped CSV under
``data/raw/<source>/<source>_<stamp>.csv`` (never overwritten). ``consolidate()`` merges
*all* pulls into one canonical daily table ``data/processed/history.parquet``, deduped on the
grain key (``KEY_COLS`` — date/channel/campaign/ids/ad_group/geo/source) keeping the
**latest pull**. It is incremental
(new pulls extend history) and idempotent (re-consolidating the same pulls yields an
identical table). The pipeline reads ``history.parquet`` — the store, not a live fetch — so
reporting is reproducible.

The pull files are RAW (canonical *schema*, but pre-cleansing): value-level messiness
(uppercased channels, NaN/negative spend) is preserved here and handled later by
``transform.clean``. Consolidation only enforces the daily grain (one row per key).
"""
from __future__ import annotations

import json
import shutil
import warnings
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import schema
from ..utils import load_mappings, project_root, standardize_channels

# Dedup grain. `campaign_id`/`account_id` disambiguate same-named campaigns across ad
# accounts; `ad_group` lets ad-set/ad-group-level rows coexist under one campaign (a
# campaign-level row has ad_group="" and is DROPPED when ad-level rows cover the same
# key — see the supersede step in consolidate); `source` lets ad rows and web-analytics
# (GA4) rows for the same (date, channel, campaign, geo) coexist — keep-latest applies
# WITHIN a source (restatements), never across sources (which would silently delete one
# side's metrics).
KEY_COLS = ("date", "channel", "campaign", "campaign_id", "account_id", "ad_group",
            "geo", "source")

# String columns normalized (fillna("") + strip) before dedup so label drift and
# NaN-vs-"" never split the grain.
_TEXT_KEY_COLS = ("campaign", "geo", "campaign_id", "account_id", "ad_group", "source",
                  "audience_type", "audience_detail", "creative", "creative_format")


def _raw_root(raw_root=None) -> Path:
    return Path(raw_root) if raw_root is not None else project_root() / "data" / "raw"


def _archive_root(archive_root=None, raw_root=None) -> Path:
    return Path(archive_root) if archive_root is not None else _raw_root(raw_root) / "_archive"


def _sidecar_path(csv_path: Path) -> Path:
    """Sidecar metadata path for a pull CSV: ``<source>_<stamp>.meta.json``."""
    return csv_path.with_suffix(".meta.json")


def _history_path(history_path=None) -> Path:
    return Path(history_path) if history_path is not None else \
        project_root() / "data" / "processed" / "history.parquet"


def _manifest_path(manifest_path=None) -> Path:
    return Path(manifest_path) if manifest_path is not None else \
        project_root() / "data" / "processed" / "history_manifest.json"


def write_pull(df: pd.DataFrame, source: str, *, raw_root=None, stamp: str | None = None) -> Path:
    """Write an immutable date-stamped pull (+ schema sidecar) and return the CSV path.

    The file is ``<raw_root>/<source>/<source>_<stamp>.csv`` (``stamp`` defaults to today,
    ``YYYYMMDD``). If a file for that stamp already exists it is NEVER overwritten — a
    ``_HHMMSS`` (then a counter) suffix is added so each pull is preserved. A
    ``<source>_<stamp>.meta.json`` sidecar records the schema signature this pull was written
    under, so ``consolidate`` can refuse to merge stale/mismatched-schema pulls.
    """
    # validate BEFORE writing — a malformed frame used to get a passing sidecar and
    # then brick the entire consolidate for every source
    schema.validate(df)
    df = df.copy()
    if "source" not in df.columns or df["source"].isna().all() \
            or (df["source"].astype(str) == "").all():
        df["source"] = source

    dest_dir = _raw_root(raw_root) / source
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now().strftime("%Y%m%d")
    path = dest_dir / f"{source}_{stamp}.csv"
    if path.exists():
        suffix = datetime.now().strftime("%H%M%S")
        path = dest_dir / f"{source}_{stamp}_{suffix}.csv"
        n = 1
        while path.exists():
            # zero-padded so 10+ same-second pulls still sort chronologically
            path = dest_dir / f"{source}_{stamp}_{suffix}-{n:03d}.csv"
            n += 1
    df.to_csv(path, index=False)

    dmin, dmax = _date_bounds(df)
    sidecar = {
        "source": source,
        "stamp": path.stem[len(source) + 1:],
        "schema_version": schema.SCHEMA_VERSION,
        "schema_signature": schema.schema_signature(),
        "rows": int(len(df)),
        "date_min": dmin, "date_max": dmax,
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    _sidecar_path(path).write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    return path


def iter_pulls(raw_root=None) -> list[tuple[str, str, Path]]:
    """All pull files as ``(stamp, source, path)`` sorted ascending (last = latest pull).

    Only files matching ``<source>/<source>_*.csv`` are included, so legacy top-level CSVs
    (e.g. ``data/raw/business_kpi_weekly.csv``) are ignored.
    """
    root = _raw_root(raw_root)
    out: list[tuple[str, str, Path]] = []
    if not root.exists():
        return out
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        source = sub.name
        if source.startswith("_"):       # skip _archive/ and other internal dirs
            continue
        for f in sub.glob(f"{source}_*.csv"):
            stamp = f.stem[len(source) + 1:]
            out.append((stamp, source, f))
    out.sort(key=lambda r: (r[0], r[1], r[2].name))
    return out


def _date_bounds(df: pd.DataFrame):
    d = pd.to_datetime(df.get("date"), errors="coerce").dropna()
    if d.empty:
        return None, None
    return d.min().date().isoformat(), d.max().date().isoformat()


def _read_sidecar(csv_path: Path) -> dict | None:
    """Return the pull's sidecar metadata, or None if it has none (legacy/unversioned)."""
    sc = _sidecar_path(csv_path)
    if not sc.exists():
        return None
    try:
        return json.loads(sc.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# Refuse a pull when more than this fraction of its rows have unparseable dates: a
# handful of stray bad rows is normal export noise (dropped + counted), but a majority
# means the file is not at daily grain at all (weekly/monthly rollups, 'Reporting
# starts'/'Reporting ends' ranges) and ingesting the parseable fragment would be the
# same silent-loss failure mode the grain collapse exists to prevent.
_MAX_BAD_DATE_FRACTION = 0.10


def _collapse_to_grain(df: pd.DataFrame) -> pd.DataFrame:
    """SUM same-key rows in ONE pull down to the store grain (``KEY_COLS``).

    An export broken down by dimensions the schema doesn't model (Age / Gender /
    Device / placement ...) delivers many rows per grain key. Within a pull those rows
    are complements of one total, so they SUM; collapsing them keep-last was silent
    data loss (a real Meta breakdown export surfaced ~3% of true spend). Cross-pull
    duplicates stay on the keep-latest restatement path in ``consolidate``.

    - ``SUMMABLE_COLUMNS`` sum with ``min_count=1``: an all-NaN group stays NaN
      ("not measured" — the schema contract), never a fabricated 0.0.
    - Remaining canonical columns (currency, audience_*, creative*,
      avg_engagement_seconds) take ``first()`` — constant within a key group on real
      exports; for ``avg_engagement_seconds`` a documented approximation.
    - Non-canonical breakdown columns (Age/Gender/...) are aggregated away entirely.
    """
    keys = list(KEY_COLS)
    agg: dict = {}
    for col in df.columns:
        if col in keys or col not in schema.CANONICAL_COLUMNS:
            continue
        agg[col] = (lambda s: s.sum(min_count=1)) \
            if col in schema.SUMMABLE_COLUMNS else "first"
    out = df.groupby(keys, as_index=False, dropna=False, sort=False).agg(agg)
    return schema.normalize(out)


def consolidate(*, raw_root=None, history_path=None, manifest_path=None,
                generated_at: str | None = None) -> dict:
    """Merge schema-MATCHING raw pulls into the canonical daily history table + manifest.

    Each pull is checked against the current schema signature (from its sidecar). Pulls whose
    signature differs — or that have no sidecar (legacy, pre-versioning) — are SKIPPED with a
    warning and recorded in ``manifest["skipped_pulls"]`` rather than silently unioned. Matching
    pulls are schema-normalized, concatenated in ascending order, deduped on ``KEY_COLS`` keeping
    the latest pull, sorted, and written to parquet. Deterministic for a fixed set of pulls.
    """
    history_path = _history_path(history_path)
    manifest_path = _manifest_path(manifest_path)
    mappings = load_mappings()
    current_sig = schema.schema_signature()

    aliases = (mappings or {}).get("channel_aliases", {})
    frames, pulls, skipped = [], [], []
    for stamp, source, path in iter_pulls(raw_root):
        meta = _read_sidecar(path)
        sig = meta.get("schema_signature") if meta else None
        if sig != current_sig:
            reason = ("no schema sidecar (legacy pull)" if meta is None
                      else f"schema signature {sig} != current {current_sig}")
            warnings.warn(f"Skipping pull {source}/{path.name}: {reason}. "
                          "Re-pull under the current schema or archive it via "
                          "`ingest.py --reset`.", stacklevel=2)
            skipped.append({"source": source, "file": path.name,
                            "schema_signature": sig, "reason": reason})
            continue
        try:
            df = schema.to_canonical(pd.read_csv(path), "default", mappings)
        except Exception as e:   # one malformed pull must not brick the whole rebuild
            reason = f"unreadable/malformed: {type(e).__name__}: {e}"
            warnings.warn(f"Skipping pull {source}/{path.name}: {reason}", stacklevel=2)
            skipped.append({"source": source, "file": path.name,
                            "schema_signature": sig, "reason": reason})
            continue
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        n_bad_dates = int(df["date"].isna().sum())
        n_rows = int(len(df))
        if n_rows and n_bad_dates > _MAX_BAD_DATE_FRACTION * n_rows:
            reason = (f"{n_bad_dates} of {n_rows} rows have unparseable dates — likely "
                      "a non-daily export (weekly/monthly rollups or 'Reporting "
                      "starts'/'Reporting ends' ranges). Refusing to ingest a fragment; "
                      "re-export at daily grain or map the date column.")
            warnings.warn(f"Skipping pull {source}/{path.name}: {reason}", stacklevel=2)
            skipped.append({"source": source, "file": path.name,
                            "schema_signature": sig, "reason": reason})
            continue

        # standardize labels BEFORE dedup: keep-latest used to run on raw labels, so a
        # restated pull with label drift ('META' vs 'facebook' vs 'meta') kept BOTH rows
        # and downstream weekly sums silently double-counted spend
        df["channel"] = standardize_channels(df["channel"], aliases)
        for col in _TEXT_KEY_COLS:
            if col in df.columns:
                df[col] = df[col].fillna("").astype(str).str.strip()
        if "source" in df.columns:
            df.loc[df["source"] == "", "source"] = source

        # same-key rows WITHIN one pull are an unmodeled breakdown (Age/Gender/Device
        # ...): complements of one total, so they are SUMMED to grain — keep-last here
        # was silent data loss. Cross-pull dups stay on the keep-latest restatement path.
        dup_keys = int(df.duplicated(subset=list(KEY_COLS)).sum())
        if dup_keys:
            warnings.warn(f"Pull {source}/{path.name}: {dup_keys} same-key row(s) "
                          "summed to grain (unmodeled breakdown aggregated away) — if "
                          "these are actually distinct campaigns, map campaign_id/"
                          "account_id so they keep separate rows.", stacklevel=2)
            df = _collapse_to_grain(df)
        frames.append(df)
        dmin, dmax = _date_bounds(df)
        pulls.append({"source": source, "file": path.name, "stamp": stamp,
                      "schema_signature": sig, "rows": n_rows,
                      "bad_date_rows": n_bad_dates, "dup_key_rows": dup_keys,
                      "date_min": dmin, "date_max": dmax})

    if frames:
        history = (pd.concat(frames, ignore_index=True)
                   .dropna(subset=["date"])
                   .drop_duplicates(subset=list(KEY_COLS), keep="last")
                   .sort_values(list(KEY_COLS))
                   .reset_index(drop=True))
    else:
        history = pd.DataFrame(columns=list(schema.CANONICAL_COLUMNS))

    # Supersede: a campaign-level row (ad_group="") is the AGGREGATE of that campaign's
    # ad-level rows. When both grains land for the same key within one source (e.g. a
    # campaign export AND an ad-set export dropped in the same inbox), keeping both would
    # double-count every metric downstream — keep the finer grain, drop its aggregate.
    superseded = 0
    if len(history) and (history["ad_group"] != "").any():
        parent_key = [c for c in KEY_COLS if c != "ad_group"]
        detail_idx = pd.MultiIndex.from_frame(
            history.loc[history["ad_group"] != "", parent_key].drop_duplicates())
        drop = ((history["ad_group"] == "").to_numpy()
                & pd.MultiIndex.from_frame(history[parent_key]).isin(detail_idx))
        superseded = int(drop.sum())
        if superseded:
            history = history.loc[~drop].reset_index(drop=True)

    history_path.parent.mkdir(parents=True, exist_ok=True)
    history.to_parquet(history_path, index=False)

    sources: dict[str, dict] = {}
    for rec in pulls:  # pulls are in ascending order, so the last seen is the latest
        s = sources.setdefault(rec["source"], {"pulls": 0, "rows_latest": 0})
        s["pulls"] += 1
        s["rows_latest"] = rec["rows"]
    manifest = {
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "schema_version": schema.SCHEMA_VERSION,
        "schema_signature": current_sig,
        "key_columns": list(KEY_COLS),
        "history_rows": int(len(history)),
        "superseded_campaign_rows": superseded,
        "history_path": str(history_path),
        "pulls": pulls,
        "skipped_pulls": skipped,
        "sources": sources,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def archive_source(source: str, *, raw_root=None, archive_root=None,
                   stamp: str | None = None) -> dict:
    """ARCHIVE (move, never delete) a source's existing pulls so a fresh pull starts clean.

    Moves every file in ``<raw_root>/<source>/`` into
    ``<archive_root>/<source>/<stamp or now>/`` and returns ``{archive_dir, moved}``. The
    durable store is the only copy of granular history once real platforms purge theirs, so
    this is reversible by design. No-op (empty ``moved``) when there is nothing to archive.
    """
    src_dir = _raw_root(raw_root) / source
    files = sorted(f for f in src_dir.glob("*") if f.is_file()) if src_dir.exists() else []
    if not files:
        return {"archive_dir": None, "moved": []}
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = _archive_root(archive_root, raw_root) / source / stamp
    dest.mkdir(parents=True, exist_ok=True)
    moved = []
    for f in files:
        shutil.move(str(f), str(dest / f.name))
        moved.append(f.name)
    return {"archive_dir": str(dest), "moved": moved}


def read_history(history_path=None) -> pd.DataFrame:
    """Read the consolidated history table, or raise a clear error if it doesn't exist yet."""
    path = _history_path(history_path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run `python scripts/ingest.py --source synthetic` to pull "
            "and consolidate the raw store first."
        )
    return pd.read_parquet(path)
