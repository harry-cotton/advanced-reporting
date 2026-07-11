"""MMM Results-page shaping tests — pure loaders over the persisted MMMResult files."""
from __future__ import annotations

import json

import pandas as pd

from advanced_reporting.dashboard import mmm_view
from advanced_reporting.dashboard.filters import toggle_channel


def _summary() -> pd.DataFrame:
    return pd.DataFrame([
        {"channel": "meta", "spend": 900.0, "contribution": 3000.0,
         "roi": 3.3, "roi_low": 1.4, "roi_high": 5.1},
        {"channel": "tiktok", "spend": 500.0, "contribution": 400.0,
         "roi": 0.8, "roi_low": 0.2, "roi_high": 1.6},
        {"channel": "linkedin", "spend": 400.0, "contribution": 120.0,
         "roi": 0.3, "roi_low": 0.1, "roi_high": 0.9},
    ])


def _meta() -> dict:
    return {"engine": "baseline", "target": "revenue",
            "fit_metrics": {"r2": 0.85, "test_r2": 0.76, "test_mape": 0.12},
            "response_curves": {"meta": {"spend": [0, 100, 200],
                                         "response": [0.0, 250.0, 380.0],
                                         "mean_spend": 120.0}},
            "dates": ["2026-01-05", "2026-01-12"],
            "actual": [10.0, 12.0], "predicted": [11.0, 11.5]}


def test_load_mmm_roundtrip_and_absence(tmp_path):
    assert mmm_view.load_mmm(tmp_path) is None            # nothing there
    _summary().to_csv(tmp_path / "channel_summary.csv", index=False)
    assert mmm_view.load_mmm(tmp_path) is None            # summary alone isn't a run
    (tmp_path / "mmm_result.json").write_text(json.dumps(_meta()), encoding="utf-8")
    run = mmm_view.load_mmm(tmp_path)
    assert run is not None and run["contributions"] is None
    assert run["meta"]["engine"] == "baseline"
    assert list(run["summary"]["channel"]) == ["meta", "tiktok", "linkedin"]


def test_waterfall_items_baseline_first_then_channels_desc():
    contrib = pd.DataFrame({"date": ["2026-01-05", "2026-01-12"],
                            "meta": [1500.0, 1500.0], "baseline": [4000.0, 4000.0]})
    items = mmm_view.waterfall_items(_summary(), contrib)
    assert items[0] == ("Baseline", 8000.0)
    assert [n for n, _v in items[1:]] == ["meta", "tiktok", "linkedin"]
    # no contributions file -> channels only, no fabricated baseline
    assert mmm_view.waterfall_items(_summary(), None)[0][0] == "meta"


def test_roi_intervals_verdicts():
    roi = mmm_view.roi_intervals(_summary()).set_index("channel")
    assert roi.loc["meta", "verdict"] == "profitable"        # whole interval >= 1
    assert roi.loc["tiktok", "verdict"] == "unproven"        # straddles 1.0
    assert roi.loc["linkedin", "verdict"] == "unprofitable"  # whole interval < 1
    assert list(roi.index) == ["meta", "tiktok", "linkedin"]  # sorted by point ROI


def test_fit_cards_lead_with_held_out():
    cards = mmm_view.fit_cards(_meta())
    labels = [c[0] for c in cards]
    assert labels[0] == "Engine"
    assert labels.index("Held-out R²") < labels.index("In-sample R²")
    assert dict(zip(labels, (c[1] for c in cards)))["Held-out MAPE"] == "12.0%"


def test_toggle_channel_click_to_focus_click_to_clear():
    assert toggle_channel(None, "meta") == ["meta"]
    assert toggle_channel([], "meta") == ["meta"]
    assert toggle_channel(["meta", "tiktok"], "meta") == ["meta"]   # narrow, not clear
    assert toggle_channel(["meta"], "meta") == []                    # same again = clear
    assert toggle_channel(["meta"], "tiktok") == ["tiktok"]          # switch focus
