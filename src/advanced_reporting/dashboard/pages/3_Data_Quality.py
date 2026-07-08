"""Data quality — the pipeline's data-quality report + store provenance (redesign R3).

Renders the structured report the pipeline writes (outputs/data_quality.md) and the
store manifest: which pulls built the history, what was skipped or superseded, and
how much ad-level spend runs under undecodable names.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
from advanced_reporting.dashboard import drilldown, insights, theme  # noqa: E402

st.set_page_config(page_title="Advanced Reporting — Data Quality", layout="wide")
theme.inject_css()
theme.nav_bar()
st.title("Data quality")

history_f = ROOT / "data" / "processed" / "history.parquet"
manifest_f = ROOT / "data" / "processed" / "history_manifest.json"
dq_f = ROOT / "outputs" / "data_quality.md"

# --- provenance tiles ---------------------------------------------------------------
if manifest_f.exists():
    man = json.loads(manifest_f.read_text(encoding="utf-8"))
    unp = (drilldown.unparsed_stats(pd.read_parquet(history_f))
           if history_f.exists() else {"spend_rate": 0.0, "names": []})
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        theme.metric_card("Pulls in store", f"{len(man.get('pulls', []))}",
                          help="Immutable raw pulls consolidated into history.parquet.")
    with c2:
        theme.metric_card("History rows", f"{man.get('history_rows', 0):,}")
    with c3:
        theme.metric_card("Superseded rows", f"{man.get('superseded_campaign_rows', 0):,}",
                          help="Campaign-level rows dropped because ad-level rows cover "
                               "the same key — prevents double-counting when both grains "
                               "are ingested.")
    with c4:
        theme.metric_card("Unparsed-name spend", f"{unp['spend_rate'] * 100:.0f}%",
                          help="Share of ad-level spend under names the naming convention "
                               "can't decode (reported, never guessed).")
    if man.get("skipped_pulls"):
        st.warning(f"{len(man['skipped_pulls'])} pull(s) skipped at consolidation "
                   "(schema mismatch or unreadable) — see the manifest below.")
    with st.expander("Store manifest (pulls, schema signature, skips)"):
        st.caption(f"Schema {man.get('schema_signature', '?')} · generated "
                   f"{man.get('generated_at', '?')}")
        st.dataframe(pd.DataFrame(man.get("pulls", [])), use_container_width=True,
                     hide_index=True)
        if man.get("skipped_pulls"):
            st.dataframe(pd.DataFrame(man["skipped_pulls"]),
                         use_container_width=True, hide_index=True)
else:
    st.caption("No store manifest yet — run `python scripts/ingest.py`.")

# --- naming-decode quality at a glance (RAG gauges) ---------------------------------
if history_f.exists():
    _unp = drilldown.unparsed_stats(pd.read_parquet(history_f))
    _rate, _cov = _unp["spend_rate"], 1.0 - _unp["spend_rate"]
    theme.action_title("Naming decode — quality at a glance",
                       "How much ad-level spend decodes cleanly to audiences/creatives.")
    _qL, _qR = st.columns(2)
    with _qL:
        theme.pace_bullet("Decode coverage", f"{_cov * 100:.0f}%",
                          fill_frac=_cov, goal_frac=1.0,
                          note=f"{_cov * 100:.0f}% of ad-level spend decodes cleanly")
    with _qR:
        _g = insights._rag_gauge(_rate, higher_is_better=False, good=0.05, warn=0.15)
        if _g:
            theme.rag_bullet("Unparsed-name spend", f"{_rate * 100:.0f}%", _g["pos"],
                             _g["band_stops"], verdict=_g["verdict"],
                             note="green ≤5% · amber ≤15% · red >15%")

st.divider()

# --- the pipeline's data-quality report ----------------------------------------------
if dq_f.exists():
    st.markdown(theme._escape_math(dq_f.read_text(encoding="utf-8")))
else:
    st.info("No data-quality report yet. Run `python scripts/run_pipeline.py` to "
            "generate `outputs/data_quality.md`.")
