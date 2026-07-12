"""The single-file HTML client report: self-contained, honest, spec-driven."""
from __future__ import annotations

import json

import pandas as pd

from advanced_reporting.agent import summaries as S
from advanced_reporting.reporting.html_report import REPORT_PATH, build_report


def _seed(root, with_spec=True, with_commentary=False, stale_commentary=False):
    proc = root / "data" / "processed"
    proc.mkdir(parents=True)
    pd.DataFrame({
        "date": pd.to_datetime(["2026-01-05", "2026-01-05", "2026-01-12",
                                "2026-01-12"]),
        "channel": ["meta", "google_search"] * 2,
        "spend": [5000.0, 3000.0, 5200.0, 2900.0],
        "impressions": [500000.0, 60000.0, 510000.0, 61000.0],
        "clicks": [6000.0, 2400.0, 6100.0, 2300.0],
        "conversions": [400.0, 120.0, 410.0, 118.0],
        "key_events": [100.0, 100.0, 105.0, 98.0],
    }).to_csv(proc / "channel_weekly_metrics.csv", index=False)
    h = S.data_hash(root)
    out = root / "outputs"
    out.mkdir()
    if with_spec:
        (out / "report_spec.json").write_text(json.dumps({
            "spec": {"campaign_type": "conversion", "primary_tier": "outcome",
                     "kpi_label": "application starts",
                     "blocks": ["cost_per_outcome", "claims_vs_measured"],
                     "watch_flags": ["meta claim ratio 4.0x — investigate"],
                     "targets": {"cost_per_key_event": {"good": 25.0,
                                                        "warn": 35.0}}},
            "meta": {"data_hash": h}}), encoding="utf-8")
    if with_commentary:
        stamped = "wrong-hash" if stale_commentary else h
        (out / "commentary_ai.md").write_text(
            "---\nstamp: s\ndata_hash: " + str(stamped) + "\n---\n\n"
            "The claim ratio is **4.0x** on meta.\n\n## Recommendations\n\n"
            "- **investigate_tracking** _(analytics-measured)_ — check the pixel.",
            encoding="utf-8")


def test_report_is_self_contained_and_spec_driven(tmp_path):
    _seed(tmp_path, with_spec=True, with_commentary=True)
    out = build_report(tmp_path)
    assert out == tmp_path / REPORT_PATH
    doc = out.read_text(encoding="utf-8")
    # self-contained: no external fetches of any kind
    assert "<script" not in doc.lower()
    assert 'src="http' not in doc and "url(http" not in doc
    assert "data:image/png;base64," in doc          # charts embedded
    # spec framing applied
    assert "application starts" in doc
    assert "meta claim ratio 4.0x" in doc           # watch flag
    # commentary embedded with the stamp
    assert "AI-drafted from computed facts" in doc
    assert "investigate_tracking" in doc


def test_report_without_spec_or_commentary_is_honest(tmp_path):
    _seed(tmp_path, with_spec=False, with_commentary=False)
    doc = build_report(tmp_path).read_text(encoding="utf-8")
    assert "Deterministic default layout" in doc
    assert "No AI commentary was published" in doc
    assert "AI-drafted from computed facts" not in doc


def test_report_excludes_stale_commentary(tmp_path):
    _seed(tmp_path, with_spec=True, with_commentary=True, stale_commentary=True)
    doc = build_report(tmp_path).read_text(encoding="utf-8")
    assert "investigate_tracking" not in doc        # stale body hidden
    assert "stale" in doc                           # with the visible note


def test_markdown_is_escaped_before_rendering(tmp_path):
    _seed(tmp_path, with_spec=False, with_commentary=False)
    # a hostile narrative can't inject markup: build with a weekly whose channel
    # name carries HTML — it must arrive escaped
    proc = tmp_path / "data" / "processed"
    df = pd.read_csv(proc / "channel_weekly_metrics.csv")
    df.loc[df["channel"] == "meta", "channel"] = "<script>alert(1)</script>"
    df.to_csv(proc / "channel_weekly_metrics.csv", index=False)
    doc = build_report(tmp_path).read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in doc
