"""The framing resolver (docs/design-intake-agent.md): per-field precedence
config > engagement > spec > default, data guard on the winning value, intake
status, and the durable engagement.yaml round-trip."""
from __future__ import annotations

import pandas as pd
import pytest

from advanced_reporting.reporting import metrics as M
from advanced_reporting.reporting.framing import (DEFAULT_KPI_LABEL,
                                                  UnconfirmedFramingError,
                                                  load_engagement, resolve,
                                                  resolve_framing,
                                                  write_engagement)


def _weekly(measured: bool = True, drop: tuple = ()) -> pd.DataFrame:
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-05", "2026-01-12"] * 2),
        "channel": ["meta", "meta", "google_search", "google_search"],
        "spend": [5000.0, 5200.0, 3000.0, 2900.0],
        "impressions": [500000.0, 510000.0, 60000.0, 61000.0],
        "clicks": [6000.0, 6100.0, 2400.0, 2300.0],
        "conversions": [400.0, 410.0, 120.0, 118.0],
        "sessions": [9000.0, 9100.0, 3000.0, 2900.0],
        "key_events": [100.0, 105.0, 100.0, 98.0],
    })
    if not measured:
        df["key_events"] = float("nan")
    return df.drop(columns=list(drop))


_ENG = {"framing": {"kpi_metric": "key_events", "kpi_label": "application starts",
                    "funnel_steps": ["impressions", "clicks", "sessions",
                                     "key_events"],
                    "targets": {"cpc": {"good": 1.0, "warn": 2.0}},
                    "client_name": "FBI", "campaign_name": "Recruiting FY26",
                    "budget": {"total": 100000, "flight_weeks": 26}}}


# ---------------------------------------------------------------- precedence
def test_config_beats_engagement_beats_spec():
    r = resolve(_weekly(), cfg={"reporting": {"kpi_label": "config label"}},
                engagement=_ENG, spec={"kpi_label": "spec label"})
    assert r.kpi_label == "config label" and r.sources["kpi_label"] == "config"
    r = resolve(_weekly(), cfg={}, engagement=_ENG,
                spec={"kpi_label": "spec label"})
    assert r.kpi_label == "application starts"
    assert r.sources["kpi_label"] == "engagement"


def test_spec_fills_gaps_only_under_a_confirmation_basis():
    spec = {"kpi_label": "sign-ups", "targets": {"cpc": {"good": 1.0}}}
    # engagement present (basis) but silent on the label -> spec fills the gap
    eng = {"framing": {"client_name": "Acme"}}
    r = resolve(_weekly(), cfg={}, engagement=eng, spec=spec)
    assert r.kpi_label == "sign-ups" and r.sources["kpi_label"] == "report_spec"
    # no basis at all -> spec judgment fields are suppressed, neutral defaults
    r2 = resolve(_weekly(), cfg={}, engagement={}, spec=spec)
    assert r2.status == "unconfirmed"
    assert r2.kpi_label == DEFAULT_KPI_LABEL and r2.targets == {}


def test_targets_merge_per_key_and_track_client_keys():
    r = resolve(_weekly(),
                cfg={"reporting": {"targets": {"cpc": {"good": 0.5}}}},
                engagement={"framing": {"targets": {"cpm": {"good": 2.0}}}},
                spec={"targets": {"ctr": {"good": 0.01}, "cpc": {"good": 9.0}}})
    assert r.targets["cpc"] == {"good": 0.5}          # config wins the shared key
    assert "cpm" in r.targets and "ctr" in r.targets  # others merge in
    assert r.client_target_keys == frozenset({"cpc", "cpm"})   # spec band excluded


# ---------------------------------------------------------------- guard-on-winner
def test_failing_config_label_falls_through_to_passing_engagement_metric():
    """Config labels the measured series (dead here); engagement labels
    conversions explicitly (alive) — the cascade lands on engagement."""
    eng = {"framing": {"kpi_metric": "conversions", "kpi_label": "purchases"}}
    r = resolve(_weekly(measured=False),
                cfg={"reporting": {"kpi_label": "application starts"}},
                engagement=eng)
    assert r.kpi_label == "purchases" and r.kpi_metric == "conversions"
    assert any(m.source == "reporting.kpi_label" for m in r.mismatches)
    assert r.status == "invalid"                      # a confirmed layer failed


def test_unplumbed_kpi_metric_is_ignored_with_note():
    r = resolve(_weekly(), cfg={},
                engagement={"framing": {"kpi_metric": "final_offers"}})
    # normalizer strips it on load; resolving the RAW dict hits the vocab guard
    assert r.kpi_metric == "key_events"
    assert any("plumbed" in m.problem for m in r.mismatches)


def test_confirmed_funnel_drops_vanished_step_and_goes_invalid():
    eng = {"framing": {"funnel_steps": ["impressions", "clicks", "sessions",
                                        "key_events"]}}
    r = resolve(_weekly(drop=("sessions",)), cfg={}, engagement=eng)
    assert r.funnel_steps == ["impressions", "clicks", "key_events"]  # order kept
    assert any(m.value == "sessions" for m in r.mismatches)
    assert r.status == "invalid"


# ---------------------------------------------------------------- status + modes
def test_lenient_config_counts_as_confirmed_strict_does_not():
    cfg = {"reporting": {"kpi_label": "application starts",
                         "budget": {"total": 1000.0}}}
    lenient = resolve(_weekly(), cfg=cfg)
    assert lenient.status == "confirmed" and lenient.hidden_blocks == frozenset()
    assert lenient.budget == {"total": 1000.0}
    strict = resolve(_weekly(), cfg={**cfg,
                                     "reporting": {**cfg["reporting"],
                                                   "intake_mode": "strict"}})
    assert strict.status == "unconfirmed"
    assert strict.kpi_label == "application starts"   # honored for rendering...
    assert strict.budget is None                      # ...but judgments neutralize
    assert strict.hidden_blocks == {"pacing", "recruiting_pipeline"}


def test_data_refresh_never_retriggers_only_guard_failures_do(tmp_path):
    (tmp_path / "data" / "processed").mkdir(parents=True)
    write_engagement(tmp_path, _ENG["framing"])
    eng, note = load_engagement(tmp_path)
    assert note is None
    # different data, everything still backed -> confirmed (hash never consulted)
    assert resolve(_weekly(), cfg={}, engagement=eng).status == "confirmed"
    # a confirmed column vanishes -> invalid, offender named
    r = resolve(_weekly(drop=("sessions",)), cfg={}, engagement=eng)
    assert r.status == "invalid"
    assert any(m.value == "sessions" for m in r.mismatches)


def test_unconfirmed_neutralizes_stages_and_budget():
    stages = pd.DataFrame({"date": pd.to_datetime(["2026-01-05"]),
                           "stage": ["screened"], "count": [10.0]})
    r = resolve(_weekly(), cfg={}, engagement={}, spec={}, stages=stages)
    assert r.status == "unconfirmed"
    assert r.stages is None and r.budget is None
    assert r.client_name is None                       # no project block passed


# ---------------------------------------------------------------- engagement I/O
def test_engagement_roundtrip_and_meta_stamp(tmp_path):
    (tmp_path / "data" / "processed").mkdir(parents=True)
    p = write_engagement(tmp_path, _ENG["framing"])
    assert p == tmp_path / "config" / "engagement.yaml"
    eng, note = load_engagement(tmp_path)
    assert note is None
    assert eng["framing"]["kpi_label"] == "application starts"
    assert eng["framing"]["targets"]["cpc"] == {"good": 1.0, "warn": 2.0}
    assert eng["meta"]["source"] == "intake_form"
    assert eng["meta"]["confirmed_at"]                  # stamped
    assert "confirmed_against_data_hash" in eng["meta"]  # provenance (None: no data)


def test_engagement_tolerant_read(tmp_path):
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "engagement.yaml").write_text(
        "framing:\n  kpi_metric: final_offers\n  mystery_key: 1\n"
        "  kpi_label: apps\n", encoding="utf-8")
    eng, note = load_engagement(tmp_path)
    assert eng["framing"]["kpi_label"] == "apps"
    assert "kpi_metric" not in eng["framing"]           # unplumbed -> unset
    assert "final_offers" in note and "mystery_key" in note


def test_engagement_corrupt_file_is_ignored_with_note(tmp_path):
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "config" / "engagement.yaml").write_text("[unclosed",
                                                         encoding="utf-8")
    eng, note = load_engagement(tmp_path)
    assert eng == {} and "unreadable" in note
    r = resolve_framing(_weekly(), tmp_path, cfg={}, spec={})
    assert r.status == "unconfirmed" and "unreadable" in r.engagement_note


# ---------------------------------------------------------------- funnel helper
def test_metrics_funnel_respects_steps_override():
    wk = _weekly()
    df = M.funnel(wk, steps=["impressions", "clicks", "key_events"])
    assert df["stage"].tolist() == ["impressions", "clicks", "key_events"]
    assert df["label"].tolist()[-1] == "Key events"     # degraded label
