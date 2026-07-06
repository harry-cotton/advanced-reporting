"""Free-text report-lens tests — deterministic parser + narrative.

Imports only reporting/lens.py (-> metrics, utils), so they run without the full pipeline.
The deterministic path is forced with use_llm=False so a present ANTHROPIC_API_KEY can't
change the outcome.
"""
import pandas as pd

from advanced_reporting.reporting import lens as L
from advanced_reporting.reporting import metrics as M


def _wk() -> pd.DataFrame:
    return pd.DataFrame({
        "channel": ["meta", "tiktok"],
        "spend": [1000.0, 500.0], "impressions": [100000.0, 50000.0],
        "clicks": [2000.0, 500.0], "conversions": [100.0, 20.0],
        "platform_revenue": [4000.0, 1500.0],
        "sessions": [1600.0, 400.0], "engaged_sessions": [800.0, 100.0],
        "page_views": [4800.0, 800.0], "video_views": [0.0, 3000.0],
    })


def test_goal_parsed_from_free_text():
    assert L.parse_lens("this is an awareness campaign", use_llm=False).goal == "awareness"
    assert L.parse_lens("drive conversions and sales", use_llm=False).goal == "conversion"
    assert L.parse_lens("we want to boost engagement", use_llm=False).goal == "consideration"
    default = M.load_campaign_goals().get("default_goal", "conversion")
    assert L.parse_lens("monthly report please", use_llm=False).goal == default


def test_primary_tier_and_metrics_follow_goal():
    s = L.parse_lens("awareness push", use_llm=False)
    assert s.primary_tier == "reach"
    reach_keys = {m["key"] for m in M.load_metric_registry() if m["tier"] == "reach"}
    assert s.metrics[0] in reach_keys                      # leads with a reach metric
    assert set(s.metrics[:len(reach_keys)]) == reach_keys  # primary tier first


def test_channels_detected_including_alias():
    s = L.parse_lens("focus on meta and tiktok for awareness", use_llm=False)
    assert set(s.channels) == {"meta", "tiktok"}
    assert L.parse_lens("facebook performance", use_llm=False).channels == ["meta"]
    assert L.parse_lens("overall conversion report", use_llm=False).channels is None


def test_tone_detection():
    assert L.parse_lens("exec summary of conversions", use_llm=False).tone == "executive"
    assert L.parse_lens("detailed breakdown of awareness", use_llm=False).tone == "detailed"
    assert L.parse_lens("conversion report", use_llm=False).tone == "standard"


def test_select_metrics_primary_tier_first():
    reg = M.load_metric_registry()
    sel = L.select_metrics("intent", reg)
    intent_keys = {m["key"] for m in reg if m["tier"] == "intent"}
    assert set(sel[:len(intent_keys)]) == intent_keys


def test_narrative_has_computed_numbers_and_caveats():
    spec = L.parse_lens("awareness campaign, detailed", use_llm=False)
    md = L.render_narrative(spec, _wk())
    assert "awareness" in md.lower()
    assert "Caveats" in md and "Funnel" in md          # detailed tone includes the funnel
    assert "%" in md or "$" in md                       # real computed values rendered


def test_narrative_respects_channel_filter():
    spec = L.parse_lens("conversions on meta", use_llm=False)
    assert spec.channels == ["meta"]
    md = L.render_narrative(spec, _wk())
    # meta-only ROAS = 4000/1000 = 4.00x (tiktok excluded)
    assert "4.00x" in md


# --- adversarial inputs (regressions for the substring-matching defects, 2026-07 review) ---

def test_campaign_word_does_not_filter_to_meta():
    # 'ig' (alias for meta) used to match inside 'campa-IG-n' — including the
    # dashboard's own placeholder text — silently scoping everything to Meta
    assert L.parse_lens("this is an awareness campaign", use_llm=False).channels is None
    assert L.parse_lens("weekly campaign report", use_llm=False).channels is None
    assert L.parse_lens("big picture overview", use_llm=False).channels is None
    assert L.parse_lens("give me insights on performance", use_llm=False).channels is None


def test_search_and_meta_substrings_do_not_leak():
    assert L.parse_lens("research report", use_llm=False).channels is None
    assert L.parse_lens("metadata analysis", use_llm=False).channels is None
    # but explicit mentions still work, in either separator style
    assert L.parse_lens("google search deep dive", use_llm=False).channels == ["google_search"]
    assert L.parse_lens("google_search results", use_llm=False).channels == ["google_search"]


def test_detailed_summary_is_detailed_not_executive():
    assert L.parse_lens("give me a detailed summary", use_llm=False).tone == "detailed"
    # boundary matching: 'brief'/'short' no longer fire inside other words
    assert L.parse_lens("the debrief on the shortfall", use_llm=False).tone == "standard"


def test_goal_inference_tokenized():
    # 'brand' no longer fires inside 'nonbrand' (compound names get token matching)
    assert M.resolve_goal("NonBrand_Search_US") == "conversion"
    # 'mid' no longer fires inside 'midwest'; 'retargeting' resolves via override/stem
    assert M.resolve_goal("midwest sales retargeting recap") == "conversion"
    # stems still work at token starts
    assert M.resolve_goal("Prospecting_Broad_Q3") == "awareness"
