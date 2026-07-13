"""A1 report-spec agent: knowledge loading, validation clipping, spec caching, and
the guarded generate path (mocked LLM — no key, no network, CI-safe)."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from advanced_reporting.agent import knowledge as K
from advanced_reporting.agent import spec_agent as SA
from advanced_reporting.agent import summaries as S
from advanced_reporting.agent.validate import BLOCK_CATALOG, validate_spec


# --- knowledge loader ---------------------------------------------------------------

def test_guidelines_load_in_briefing_order():
    docs = K.load_guidelines()
    names = list(docs)
    assert names[:len(K.GUIDELINE_ORDER)] == list(K.GUIDELINE_ORDER)
    assert all(docs[n].strip() for n in names)


def test_context_excludes_readme_and_templates():
    names = set(K.load_context())
    assert not any(n.lower().startswith("readme") for n in names)
    assert not any(".template." in n for n in names)


def test_as_block_empty_is_explicit():
    assert K.as_block({}) == "(none provided)"


# --- validation: clip, never trust ----------------------------------------------------

def test_validate_keeps_valid_fields():
    spec, dropped = validate_spec({
        "campaign_type": "awareness", "primary_tier": "reach",
        "kpi_label": "  start applications ",
        "targets": [{"metric": "cpm", "good": 2.0, "warn": 4.0}],
        "blocks": ["claims_vs_measured", "kpi_trend"],
        "watch_flags": ["claim ratio 3.9x on meta"],
        "rationale": "names carry AWARENESS tokens",
    })
    assert dropped == []
    assert spec["campaign_type"] == "awareness"
    assert spec["kpi_label"] == "start applications"
    assert spec["targets"] == {"cpm": {"good": 2.0, "warn": 4.0}}
    assert spec["blocks"] == ["claims_vs_measured", "kpi_trend"]


def test_validate_drops_invalid_loudly():
    spec, dropped = validate_spec({
        "campaign_type": "branding",              # not a type
        "primary_tier": "funnel",                 # not a tier
        "targets": [{"metric": "made_up_metric", "good": 1.0},
                    {"metric": "cpc"}],           # known key, no usable bands
        "blocks": ["kpi_trend", "kpi_trend", "made_up_block"],
        "watch_flags": ["a", "b", "c", "d", "e"],  # over the cap
    })
    assert spec.get("campaign_type") is None
    assert spec.get("primary_tier") is None
    assert "targets" not in spec
    assert spec["blocks"] == ["kpi_trend"]        # dedup + unknown dropped
    assert len(spec["watch_flags"]) == 3
    joined = " ".join(dropped)
    for needle in ("branding", "funnel", "made_up_metric", "cpc",
                   "made_up_block", "watch_flags"):
        assert needle in joined, f"drop reason missing for {needle}"


def test_validate_accepts_dict_targets_for_robustness():
    spec, _ = validate_spec({"targets": {"ctr": {"good": 0.02, "warn": 0.01}}})
    assert spec["targets"] == {"ctr": {"good": 0.02, "warn": 0.01}}


def test_validate_garbage_spec_degrades_to_empty():
    spec, dropped = validate_spec("not even a dict")
    assert spec == {} and dropped


# --- spec cache: hash keying -----------------------------------------------------------

def _seed_data(root, rows=3) -> None:
    proc = root / "data" / "processed"
    proc.mkdir(parents=True)
    pd.DataFrame({
        "date": pd.date_range("2026-01-05", periods=rows, freq="W-MON"),
        "channel": ["meta"] * rows, "spend": [100.0] * rows,
        "impressions": [10000.0] * rows, "clicks": [200.0] * rows,
        "conversions": [10.0] * rows, "key_events": [8.0] * rows,
    }).to_csv(proc / "channel_weekly_metrics.csv", index=False)


def _write_spec(root, spec: dict, data_hash) -> None:
    out = root / "outputs"
    out.mkdir(exist_ok=True)
    (out / "report_spec.json").write_text(json.dumps(
        {"spec": spec, "meta": {"data_hash": data_hash}}), encoding="utf-8")


def test_load_active_spec_absent_is_silent(tmp_path):
    assert SA.load_active_spec(tmp_path) == ({}, None)


def test_load_active_spec_current(tmp_path):
    _seed_data(tmp_path)
    _write_spec(tmp_path, {"campaign_type": "conversion", "primary_tier": "outcome"},
                S.data_hash(tmp_path))
    spec, note = SA.load_active_spec(tmp_path)
    assert note is None
    assert spec["campaign_type"] == "conversion"


def test_load_active_spec_stale_hash_ignored_with_note(tmp_path):
    _seed_data(tmp_path)
    _write_spec(tmp_path, {"campaign_type": "conversion"}, "not-the-hash")
    spec, note = SA.load_active_spec(tmp_path)
    assert spec == {}
    assert note and "stale" in note


def test_load_active_spec_revalidates_on_read(tmp_path):
    # user-edited file with an out-of-vocab value must be clipped on the way in
    _seed_data(tmp_path)
    _write_spec(tmp_path, {"campaign_type": "vibes", "primary_tier": "reach"},
                S.data_hash(tmp_path))
    spec, _ = SA.load_active_spec(tmp_path)
    assert "campaign_type" not in spec
    assert spec["primary_tier"] == "reach"


def test_load_active_spec_unreadable_file(tmp_path):
    _seed_data(tmp_path)
    out = tmp_path / "outputs"
    out.mkdir()
    (out / "report_spec.json").write_text("{broken", encoding="utf-8")
    spec, note = SA.load_active_spec(tmp_path)
    assert spec == {} and note


def test_data_hash_none_without_processed_data(tmp_path):
    assert S.data_hash(tmp_path) is None


def test_data_hash_changes_with_data(tmp_path):
    _seed_data(tmp_path, rows=3)
    h1 = S.data_hash(tmp_path)
    (tmp_path / "data" / "processed" / "channel_weekly_metrics.csv").unlink()
    _seed_data_replace(tmp_path)
    assert S.data_hash(tmp_path) != h1


def _seed_data_replace(root) -> None:
    proc = root / "data" / "processed"
    pd.DataFrame({
        "date": ["2026-02-02"], "channel": ["tiktok"], "spend": [999.0],
        "impressions": [1.0], "clicks": [1.0], "conversions": [1.0],
        "key_events": [1.0],
    }).to_csv(proc / "channel_weekly_metrics.csv", index=False)


# --- compact summaries -----------------------------------------------------------------

def test_data_summary_shape(tmp_path):
    _seed_data(tmp_path)
    s = S.data_summary(tmp_path)
    assert s["n_weeks"] == 3
    assert s["measured_outcome_exists"] is True
    meta = s["paid_channel_rollups"]["meta"]
    assert meta["spend"] == 300
    assert meta["claim_ratio"] == pytest.approx(30 / 24, abs=0.01)


def test_data_summary_none_without_data(tmp_path):
    assert S.data_summary(tmp_path) is None


# --- generate path (mocked LLM) ---------------------------------------------------------

def _seed_prompt_template(root) -> None:
    p = root / "system" / "prompts"
    p.mkdir(parents=True)
    (p / "spec_agent.md").write_text(
        "G:{guidelines}\nC:{context}\nD:{data_summary}\nCAT:{catalog}",
        encoding="utf-8")


def test_generate_spec_no_data_explains_itself(tmp_path, monkeypatch):
    _seed_prompt_template(tmp_path)
    spec, info = SA.generate_spec(tmp_path)
    assert spec is None
    assert "no processed data" in info["error"]


def test_generate_spec_agent_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(SA, "load_config", lambda: {"agent": {"enabled": False}})
    spec, info = SA.generate_spec(tmp_path)
    assert spec is None
    assert "enabled" in info["error"]


def test_generate_spec_call_failure_writes_nothing(tmp_path, monkeypatch):
    _seed_data(tmp_path)
    _seed_prompt_template(tmp_path)
    monkeypatch.setattr(SA, "call",
                        lambda *a, **k: (None, {"error": "no ANTHROPIC_API_KEY"}))
    spec, info = SA.generate_spec(tmp_path)
    assert spec is None
    assert not (tmp_path / "outputs" / "report_spec.json").exists()


def test_generate_spec_mocked_roundtrip(tmp_path, monkeypatch):
    _seed_data(tmp_path)
    _seed_prompt_template(tmp_path)
    raw = {"campaign_type": "conversion", "primary_tier": "outcome",
           "kpi_label": "start applications",
           "targets": [{"metric": "cost_per_key_event", "good": 50.0, "warn": 90.0},
                       {"metric": "bogus", "good": 1.0}],
           "blocks": ["claims_vs_measured", "cost_per_outcome"],
           "watch_flags": ["claim ratio 1.25x — normal"],
           "rationale": "conversion wiring present"}

    def fake_call(prompt, *, model, schema=None, **kw):
        # the prompt must carry the template tokens filled in
        assert "CAT:" + ", ".join(BLOCK_CATALOG) in prompt
        assert schema is not None
        return raw, {"model": model, "input_tokens": 10, "output_tokens": 5,
                     "cost_usd": 0.001, "error": None}

    monkeypatch.setattr(SA, "call", fake_call)
    spec, info = SA.generate_spec(tmp_path, model="claude-sonnet-5")
    assert spec["kpi_label"] == "start applications"
    assert spec["targets"] == {"cost_per_key_event": {"good": 50.0, "warn": 90.0}}
    assert any("bogus" in d for d in info["dropped"])

    # written artifact is hash-stamped and immediately loadable
    payload = json.loads((tmp_path / "outputs" / "report_spec.json").read_text())
    assert payload["meta"]["data_hash"] == S.data_hash(tmp_path)
    loaded, note = SA.load_active_spec(tmp_path)
    assert note is None and loaded["campaign_type"] == "conversion"


# --- config-merge semantics (the dashboard's gap-fill rule, unit level) -----------------

def test_explicit_config_wins_per_target_key():
    spec_targets = {"cpm": {"good": 2.0, "warn": 4.0}, "ctr": {"good": 0.02}}
    cfg_targets = {"cpm": {"good": 1.0, "warn": 3.0}}
    merged = {**spec_targets, **cfg_targets}
    assert merged["cpm"] == {"good": 1.0, "warn": 3.0}   # config wins
    assert merged["ctr"] == {"good": 0.02}               # spec fills the gap
