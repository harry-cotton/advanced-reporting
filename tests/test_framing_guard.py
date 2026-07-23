"""The framing guard: configured KPI / targets / funnel must exist in the loaded data
(the CLIENTXYZ finding, docs/notes-intake-agent.md — stale FBI config framed another
client's report and nothing checked)."""
from __future__ import annotations

import pandas as pd
import pytest

from advanced_reporting.reporting.framing import (DEFAULT_KPI_LABEL, FramingError,
                                                  guard_framing, require_clean)


def _weekly(measured: bool) -> pd.DataFrame:
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-05", "2026-01-12"] * 2),
        "channel": ["meta", "meta", "google_search", "google_search"],
        "spend": [5000.0, 5200.0, 3000.0, 2900.0],
        "impressions": [500000.0, 510000.0, 60000.0, 61000.0],
        "clicks": [6000.0, 6100.0, 2400.0, 2300.0],
        "conversions": [400.0, 410.0, 120.0, 118.0],
        "key_events": [100.0, 105.0, 100.0, 98.0],
    })
    if not measured:
        df["key_events"] = float("nan")     # ad exports only — nothing analytics-measured
    return df


def test_clean_framing_passes_through():
    rep = {"kpi_label": "application starts",
           "targets": {"cost_per_key_event": {"good": 60.0, "warn": 80.0}}}
    f = guard_framing(_weekly(measured=True), rep=rep, spec={})
    assert f.mismatches == []
    assert f.kpi_label == "application starts"
    assert "cost_per_key_event" in f.targets
    require_clean(f)                        # does not raise


def test_stale_kpi_label_is_dropped_when_nothing_is_measured():
    """The exact CLIENTXYZ failure: an inherited kpi_label with no measured series."""
    f = guard_framing(_weekly(measured=False),
                      rep={"kpi_label": "application starts"}, spec={})
    assert f.kpi_label == DEFAULT_KPI_LABEL
    assert any(m.source == "reporting.kpi_label" for m in f.mismatches)
    with pytest.raises(FramingError, match="application starts"):
        require_clean(f)


def test_spec_kpi_label_is_guarded_too_and_source_named():
    f = guard_framing(_weekly(measured=False), rep={},
                      spec={"kpi_label": "sign-ups"})
    assert f.kpi_label == DEFAULT_KPI_LABEL
    assert any(m.source == "report_spec.kpi_label" for m in f.mismatches)


def test_default_label_never_flags():
    """No configured label (or the literal default) on unmeasured data is the honest
    fallback path, not a mismatch — the guard only fires on stale CLAIMS."""
    assert guard_framing(_weekly(measured=False), rep={}, spec={}).mismatches == []
    assert guard_framing(_weekly(measured=False),
                         rep={"kpi_label": "key events"}, spec={}).mismatches == []


def test_dead_target_is_dropped_live_target_survives():
    """A target graded on a metric with no data behind it is stale config."""
    rep = {"targets": {"cost_per_key_event": {"good": 60.0, "warn": 80.0},
                       "cpc": {"good": 1.0, "warn": 2.0}}}
    f = guard_framing(_weekly(measured=False), rep=rep, spec={})
    assert "cpc" in f.targets                              # clicks exist -> lives
    assert "cost_per_key_event" not in f.targets           # key_events empty -> dead
    assert any("cost_per_key_event" in m.source for m in f.mismatches)


def test_disjoint_stage_window_drops_the_funnel():
    """An applicant-pipeline file whose dates never overlap the data is another
    engagement's leftover — the funnel must not render from it."""
    stages = pd.DataFrame({
        "date": pd.to_datetime(["2024-03-04", "2024-03-11"]),
        "stage": ["screened", "final_offer"], "count": [100.0, 5.0]})
    wk = _weekly(measured=True)             # data lives in 2026
    f = guard_framing(wk, rep={}, spec={}, stages=stages)
    assert f.stages is None
    assert any(m.source == "data.pipeline_stages_path" for m in f.mismatches)

    overlapping = stages.assign(date=pd.to_datetime(["2026-01-05", "2026-01-12"]))
    f2 = guard_framing(wk, rep={}, spec={}, stages=overlapping)
    assert f2.stages is not None and f2.mismatches == []


def test_build_report_refuses_stale_framing(tmp_path, monkeypatch):
    """The shippable artifact fails LOUD: build_report raises FramingError (with the
    stale label named) instead of emailing a report framed around a dead metric."""
    proc = tmp_path / "data" / "processed"
    proc.mkdir(parents=True)
    _weekly(measured=False).to_csv(proc / "channel_weekly_metrics.csv", index=False)
    (tmp_path / "outputs").mkdir()
    from advanced_reporting.reporting import html_report as HR
    monkeypatch.setattr(HR, "load_config", lambda: {
        "reporting": {"kpi_label": "application starts"},
        "project": {"name": "Stale-config engagement"}})
    with pytest.raises(FramingError, match="application starts"):
        HR.build_report(tmp_path)


def test_build_report_gate_and_draft_watermark(tmp_path, monkeypatch):
    """Unconfirmed -> refuses with a Setup pointer; --allow-unconfirmed -> DRAFT
    watermarks (title + stamp); confirming via engagement.yaml -> clean build."""
    from advanced_reporting.reporting import html_report as HR
    from advanced_reporting.reporting.framing import (UnconfirmedFramingError,
                                                      write_engagement)
    proc = tmp_path / "data" / "processed"
    proc.mkdir(parents=True)
    _weekly(measured=True).to_csv(proc / "channel_weekly_metrics.csv", index=False)
    (tmp_path / "outputs").mkdir()
    monkeypatch.setattr(HR, "load_config",
                        lambda: {"project": {"name": "T"}, "reporting": {}})
    with pytest.raises(UnconfirmedFramingError, match="Setup"):
        HR.build_report(tmp_path)
    doc = HR.build_report(tmp_path, allow_unconfirmed=True) \
        .read_text(encoding="utf-8")
    assert "DRAFT — framing unconfirmed" in doc
    assert "<title>DRAFT — " in doc
    write_engagement(tmp_path, {"kpi_metric": "key_events",
                                "kpi_label": "application starts"})
    doc2 = HR.build_report(tmp_path).read_text(encoding="utf-8")
    assert "DRAFT" not in doc2
    assert "application starts" in doc2


def test_invalid_draft_never_leaks_the_stale_label(tmp_path, monkeypatch):
    """An invalid engagement built with --allow-unconfirmed is watermarked AND
    framed neutrally — the contradicted label appears nowhere in the artifact."""
    from advanced_reporting.reporting import html_report as HR
    proc = tmp_path / "data" / "processed"
    proc.mkdir(parents=True)
    _weekly(measured=False).to_csv(proc / "channel_weekly_metrics.csv", index=False)
    (tmp_path / "outputs").mkdir()
    monkeypatch.setattr(HR, "load_config",
                        lambda: {"project": {"name": "T"},
                                 "reporting": {"kpi_label": "application starts"}})
    doc = HR.build_report(tmp_path, allow_unconfirmed=True) \
        .read_text(encoding="utf-8")
    assert "DRAFT — framing unconfirmed" in doc
    assert "application starts" not in doc


def test_explicit_config_still_wins_over_spec():
    f = guard_framing(_weekly(measured=True),
                      rep={"kpi_label": "applications"},
                      spec={"kpi_label": "sign-ups"})
    assert f.kpi_label == "applications"
