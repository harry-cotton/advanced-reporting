"""Extraction CLI: pull a source via the extraction contract into the durable raw store.

    python scripts/ingest.py --source synthetic [--start 2024-07-01 --end 2026-06-28]

Calls ``get_source(name).fetch(start, end)``, writes the returned canonical rows as an
immutable, date-stamped pull under ``data/raw/<source>/<source>_YYYYMMDD.csv`` (never
overwritten), then consolidates all pulls into ``data/processed/history.parquet`` + manifest.

``--source google_ads`` (etc.) works unchanged once that connector is implemented; today the
skeletons raise a clear NotImplementedError. ``--consolidate-only`` rebuilds history from the
existing pulls without fetching.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from advanced_reporting.utils import load_config
from advanced_reporting.ingestion.factory import get_source
from advanced_reporting.ingestion import store


def main(argv=None) -> None:
    cfg = load_config()
    d = cfg.get("data", {})
    ap = argparse.ArgumentParser(description="Pull a source into the durable raw store.")
    ap.add_argument("--source", default=d.get("source", "synthetic"),
                    help="data source name (synthetic, csv, google_ads, meta, tiktok, linkedin, ...)")
    ap.add_argument("--start", default=None, help="ISO start date (overrides config data.start)")
    ap.add_argument("--end", default=None, help="ISO end date (overrides config data.end)")
    ap.add_argument("--inbox", action="store_true",
                    help="ingest manually-downloaded export files from data/inbox/ "
                         "(Google Ads / Meta / LinkedIn / GA4; format auto-detected)")
    ap.add_argument("--consolidate-only", action="store_true",
                    help="rebuild history.parquet from existing pulls without fetching")
    ap.add_argument("--reset", "--fresh", action="store_true", dest="reset",
                    help="ARCHIVE this source's existing pulls (move to data/raw/_archive/, "
                         "never delete) BEFORE pulling fresh — use after a schema change")
    args = ap.parse_args(argv)

    if args.reset and not args.consolidate_only:
        res = store.archive_source(args.source)
        if res["moved"]:
            print(f"Archived {len(res['moved'])} file(s) from '{args.source}' -> "
                  f"{Path(res['archive_dir']).relative_to(ROOT)}")
        else:
            print(f"Reset: nothing to archive for '{args.source}'.")

    if args.inbox:
        from advanced_reporting.ingestion import naming_decode
        from advanced_reporting.ingestion.exports import read_export
        inbox = ROOT / "data" / "inbox"
        files = sorted(f for f in inbox.glob("*.csv") if not f.name.startswith("_"))
        if not files:
            print(f"Inbox {inbox.relative_to(ROOT)} has no .csv files — drop platform "
                  "exports there (see data/inbox/README.md).")
            return
        ok = 0
        for f in files:
            try:
                source, df = read_export(f)
            except Exception as e:            # one bad file must not block the rest
                print(f"  SKIPPED {f.name}: {e}")
                continue
            path = store.write_pull(df, source)
            print(f"  {f.name} -> {source}: {len(df):,} rows -> {path.relative_to(ROOT)}")
            if "ad_group" in df.columns:
                ad_level = df[df["ad_group"].fillna("") != ""]
                if len(ad_level):
                    rate = naming_decode.unparsed_rate(df)
                    print(f"    ad-level grain: {ad_level['ad_group'].nunique()} ad "
                          f"groups; naming-convention unparsed rate {rate:.0%}")
            ok += 1
        print(f"Ingested {ok}/{len(files)} inbox file(s).")
    elif not args.consolidate_only:
        start = args.start or d.get("start")
        end = args.end or d.get("end")
        kwargs = {}
        if args.source == "synthetic" and d.get("geos"):
            kwargs["geos"] = d["geos"]
        if args.source == "csv":
            kwargs["path"] = ROOT / "data/raw/ad_platform_daily.csv"

        df = get_source(args.source, **kwargs).fetch(start, end)
        path = store.write_pull(df, args.source)
        print(f"Pulled {len(df):,} rows from '{args.source}' "
              f"[{start or 'all'} -> {end or 'all'}] -> {path.relative_to(ROOT)}")

    manifest = store.consolidate()
    print(f"Consolidated {len(manifest['pulls'])} pull(s) -> "
          f"{manifest['history_rows']:,} canonical daily rows in "
          f"{Path(manifest['history_path']).relative_to(ROOT)}")
    if manifest.get("superseded_campaign_rows"):
        print(f"  {manifest['superseded_campaign_rows']:,} campaign-level row(s) "
              "superseded by ad-level rows for the same campaign (finer grain kept; "
              "no double-counting)")
    for src, info in manifest["sources"].items():
        print(f"  {src:<12} {info['pulls']} pull(s), latest {info['rows_latest']:,} rows")
    if manifest["skipped_pulls"]:
        print(f"  skipped {len(manifest['skipped_pulls'])} mismatched/legacy pull(s) "
              "(schema signature differs) — re-pull or archive via --reset")


if __name__ == "__main__":
    main()
