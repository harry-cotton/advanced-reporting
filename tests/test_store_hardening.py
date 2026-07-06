"""Store-hardening regressions (2026-07 review, Step-7 subset pulled forward for the
file-drop ingestion milestone): label-drift double-counting, GA4 coexistence, per-pull
isolation, grain identity columns, currency discipline."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from advanced_reporting.ingestion import schema, store
from advanced_reporting.utils import load_mappings


def _ad_rows(rows, **over):
    base = pd.DataFrame([{
        "date": d, "channel": ch, "campaign": camp, "geo": "national",
        "spend": sp, "impressions": 1000.0, "clicks": 50.0,
        "conversions": 5.0, "platform_revenue": 500.0, "currency": "USD",
    } for d, ch, camp, sp in rows])
    for k, v in over.items():
        base[k] = v
    return base


def _consolidate(tmp_path):
    return store.consolidate(raw_root=tmp_path / "raw",
                             history_path=tmp_path / "history.parquet",
                             manifest_path=tmp_path / "manifest.json")


def test_label_drift_restatement_does_not_double_count(tmp_path):
    raw = tmp_path / "raw"
    # pull 1 labels the channel 'META'; a restatement re-delivers the same day as
    # 'facebook' with corrected spend — different raw keys used to BOTH survive dedup,
    # then standardize to 'meta' in clean and get SUMMED (silent double count)
    store.write_pull(_ad_rows([("2026-01-05", "META", "camp_a", 100.0)]),
                     "meta_ads", raw_root=raw, stamp="20260106")
    store.write_pull(_ad_rows([("2026-01-05", "facebook", "camp_a", 140.0)]),
                     "meta_ads", raw_root=raw, stamp="20260107")
    _consolidate(tmp_path)
    hist = pd.read_parquet(tmp_path / "history.parquet")
    assert len(hist) == 1                                # one row, not two
    assert hist["channel"].iloc[0] == "meta"             # standardized before dedup
    assert hist["spend"].iloc[0] == 140.0                # latest pull wins


def test_ga4_rows_coexist_with_ad_rows_on_same_grain(tmp_path):
    raw = tmp_path / "raw"
    store.write_pull(_ad_rows([("2026-01-05", "meta", "camp_a", 100.0)]),
                     "meta_ads", raw_root=raw, stamp="20260106")
    ga4 = schema.to_canonical(pd.DataFrame({
        "date": ["2026-01-05"], "channel": ["meta"], "campaign": ["camp_a"],
        "sessions": [400.0], "engaged_sessions": [220.0],
    }), "ga4", load_mappings())
    store.write_pull(ga4, "ga4", raw_root=raw, stamp="20260107")
    _consolidate(tmp_path)
    hist = pd.read_parquet(tmp_path / "history.parquet")
    # the old (date, channel, campaign, geo) grain kept ONE of these rows — whichever
    # pull sorted later silently deleted the other side's metrics
    assert len(hist) == 2
    by_source = hist.set_index("source")
    assert by_source.loc["meta_ads", "spend"] == 100.0
    assert pd.isna(by_source.loc["ga4", "spend"])         # not measured, not zero
    assert by_source.loc["ga4", "sessions"] == 400.0


def test_web_analytics_source_fills_ad_metrics_and_claims_no_currency():
    out = schema.to_canonical(pd.DataFrame({
        "date": ["2026-01-05"], "channel": ["google_search"], "campaign": ["brand"],
        "sessions": [120.0],
    }), "ga4", load_mappings())
    assert all(pd.isna(out[c].iloc[0]) for c in schema.METRIC_COLUMNS)
    assert pd.isna(out["currency"].iloc[0])               # no USD stamped on analytics


def test_real_source_without_currency_is_refused():
    df = pd.DataFrame({
        "date": ["2026-01-05"], "channel": ["linkedin"], "campaign": ["c"],
        "spend": [10.0], "impressions": [1.0], "clicks": [1.0],
        "conversions": [0.0], "platform_revenue": [0.0],
    })
    with pytest.raises(schema.SchemaError, match="currency"):
        schema.to_canonical(df, "linkedin", load_mappings())
    out = schema.to_canonical(df, "linkedin", load_mappings(), currency="EUR")
    assert out["currency"].iloc[0] == "EUR"               # explicit is fine


def test_malformed_pull_is_skipped_not_bricking(tmp_path):
    raw = tmp_path / "raw"
    store.write_pull(_ad_rows([("2026-01-05", "meta", "camp_a", 100.0)]),
                     "meta_ads", raw_root=raw, stamp="20260106")
    # a corrupt pull with a valid-looking sidecar (write_pull would refuse it, so
    # simulate a file corrupted after writing)
    bad_dir = raw / "junk"
    bad_dir.mkdir(parents=True)
    (bad_dir / "junk_20260107.csv").write_text("not,a,canonical\n1,2,3\n", encoding="utf-8")
    (bad_dir / "junk_20260107.meta.json").write_text(json.dumps({
        "source": "junk", "schema_signature": schema.schema_signature()}), encoding="utf-8")
    with pytest.warns(UserWarning, match="malformed"):
        manifest = _consolidate(tmp_path)
    assert len(pd.read_parquet(tmp_path / "history.parquet")) == 1   # good pull survives
    assert any("malformed" in s["reason"] for s in manifest["skipped_pulls"])


def test_write_pull_validates_before_writing(tmp_path):
    with pytest.raises(schema.SchemaError):
        store.write_pull(pd.DataFrame({"date": ["2026-01-05"], "oops": [1]}),
                         "meta_ads", raw_root=tmp_path / "raw")
    assert not (tmp_path / "raw" / "meta_ads").exists()   # nothing written


def test_same_named_campaigns_in_different_accounts_both_survive(tmp_path):
    raw = tmp_path / "raw"
    df = _ad_rows([("2026-01-05", "meta", "MBA_Prospecting", 100.0),
                   ("2026-01-05", "meta", "MBA_Prospecting", 250.0)])
    df["account_id"] = ["acct_grad_school", "acct_exec_ed"]
    store.write_pull(df, "meta_ads", raw_root=raw, stamp="20260106")
    _consolidate(tmp_path)
    hist = pd.read_parquet(tmp_path / "history.parquet")
    assert len(hist) == 2                                 # old grain collapsed to 1
    assert hist["spend"].sum() == 350.0


def test_within_pull_key_collision_warns(tmp_path):
    raw = tmp_path / "raw"
    df = _ad_rows([("2026-01-05", "meta", "MBA_Prospecting", 100.0),
                   ("2026-01-05", "meta", "MBA_Prospecting", 250.0)])  # no account ids
    store.write_pull(df, "meta_ads", raw_root=raw, stamp="20260106")
    with pytest.warns(UserWarning, match="same-key"):
        manifest = _consolidate(tmp_path)
    assert manifest["pulls"][0]["dup_key_rows"] == 1
