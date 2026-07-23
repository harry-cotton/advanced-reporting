"""Setup — intake: the agent proposes a framing from the LOADED data, the human
confirms; answers become durable per-engagement config (config/engagement.yaml).

The brief's invariant (docs/design-intake-agent.md): the form can only offer what
exists in the data. Zone A shows the facts (trust anchor); Zone B asks exactly four
business judgments. Deterministic without an API key — a key only upgrades the
proposal's prose, never its values.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
from advanced_reporting.agent import intake  # noqa: E402
from advanced_reporting.agent import load_active_spec  # noqa: E402
from advanced_reporting.dashboard import insights, theme  # noqa: E402
from advanced_reporting.reporting.framing import (resolve_framing,  # noqa: E402
                                                  write_engagement)
from advanced_reporting.utils import load_config, load_pipeline_stages  # noqa: E402

st.set_page_config(page_title="Advanced Reporting — Setup", layout="wide")
theme.inject_css()
theme.nav_bar()

st.title("Setup")
st.caption("Confirm how this engagement's report is framed. The form only offers "
           "what exists in the loaded data; your answers are saved to "
           "`config/engagement.yaml` and survive data refreshes.")

metrics_f = ROOT / "data" / "processed" / "channel_weekly_metrics.csv"
if not metrics_f.exists():
    st.warning("No processed data yet — run `python scripts/run_pipeline.py` first. "
               "Intake proposes from the actual data, so there is nothing to "
               "propose from until a pipeline run lands.")
    st.stop()


@st.cache_data
def _load_weekly(path: str, mtime: float) -> pd.DataFrame:
    return pd.read_csv(path, parse_dates=["date"])


weekly = _load_weekly(str(metrics_f), metrics_f.stat().st_mtime)
cfg = load_config()
spec, _spec_note = load_active_spec(ROOT)
stages = load_pipeline_stages(cfg, ROOT)
res = resolve_framing(weekly, ROOT, cfg=cfg, spec=spec, stages=stages)
theme.intake_banner(res)

# --- status line -------------------------------------------------------------------
eng_meta = {}
try:
    from advanced_reporting.reporting.framing import load_engagement
    eng_meta = (load_engagement(ROOT)[0] or {}).get("meta") or {}
except Exception:
    pass
if res.status == "confirmed":
    _when = eng_meta.get("confirmed_at")
    _hash = str(eng_meta.get("confirmed_against_data_hash") or "")[:10]
    if _when:
        st.caption(f"✔ Framing confirmed {_when}"
                   + (f" · against data {_hash}…" if _hash else "")
                   + " — edit below and re-confirm to update.")
    else:
        st.caption("✔ Framing comes from hand-edited `config.yaml` (escape hatch) "
                   "— confirming below writes `engagement.yaml` instead.")

# --- Zone A: what's in your data (read-only facts) ---------------------------------
facts = intake.intake_facts(ROOT, weekly, resolved=res)
cov = facts.get("outcome_coverage") or {}
summary = facts.get("summary") or {}

st.subheader("What's in your data")
c1, c2, c3, c4 = st.columns(4)
lo, hi = weekly["date"].min(), weekly["date"].max()
c1.metric("Date range", f"{lo:%b %Y} – {hi:%b %Y}")
c2.metric("Weeks", f"{summary.get('n_weeks', weekly['date'].nunique())}")
c3.metric("Paid channels", f"{len(insights._paid_channels(weekly))}")
c4.metric("Currency", facts.get("currency", "USD"))

rollups = summary.get("paid_channel_rollups") or {}
if rollups:
    _mix = pd.DataFrame([{"channel": insights.channel_label(ch),
                          "spend": v.get("spend", 0.0)}
                         for ch, v in rollups.items()])
    _mix = _mix.sort_values("spend", ascending=False)
    _tot = float(_mix["spend"].sum()) or 1.0
    _mix["share"] = _mix["spend"] / _tot
    st.dataframe(_mix, hide_index=True, use_container_width=True,
                 column_config={
                     "channel": "Channel",
                     "spend": st.column_config.NumberColumn("Spend", format="$%d"),
                     "share": st.column_config.ProgressColumn(
                         "Share", format="%.0f%%", min_value=0, max_value=1)})

_out_lines = []
for col, c in cov.items():
    if c["present"]:
        _out_lines.append(f"- `{col}` — **{c['kind']}**, data in "
                          f"{c['weeks_with_data']}/{c['weeks_total']} weeks")
    else:
        _out_lines.append(f"- `{col}` — not present in this data")
st.markdown("**Outcome-like columns**\n" + "\n".join(_out_lines))
st.markdown("**Funnel-candidate columns (tier order):** "
            + (" → ".join(f"`{c}`" for c in facts.get("funnel_candidates", []))
               or "_none beyond spend/impressions_"))

if facts.get("mismatches"):
    st.warning("**Configured but not backed by this data:**\n"
               + "\n".join(f"- {m}" for m in facts["mismatches"]))

dq = (summary.get("data_quality_report") or "").strip()
if dq:
    with st.expander("Data-quality flags"):
        st.markdown(dq[:1500])

# --- proposal line -----------------------------------------------------------------
proposal = intake.propose_framing(weekly, facts)
_narrative, _label_suggestion = intake.narrate_proposal(proposal, facts)
st.subheader("Confirm the framing")
st.markdown(_narrative or intake.proposal_sentence(proposal, cov))
if _narrative:
    st.caption("AI-narrated from computed facts — the proposed values are "
               "deterministic and were not chosen by the model.")

# --- Zone B: the four questions ----------------------------------------------------
def _prefill(field, fallback):
    """Confirmed/hand-config answers win the prefill; else the proposal."""
    return getattr(res, field) if res.sources.get(field) != "default" else fallback


_options = [m for m in intake.PLUMBED_KPI_METRICS
            if (cov.get(m) or {}).get("present")]
if not _options:
    st.error("No outcome-like column (`key_events` / `conversions`) has data — "
             "there is nothing to frame a report around. Check ingestion.")
    st.stop()

_cur_metric = _prefill("kpi_metric", proposal["kpi_metric"])
_cur_label = _prefill("kpi_label", _label_suggestion or proposal["kpi_label"])
_cur_steps = _prefill("funnel_steps", proposal["funnel_steps"])
_cur_cost_band = res.targets.get(intake.COST_PER_KEY.get(_cur_metric, ""), {})
_cur_goal = (res.targets.get(_cur_metric) or {}).get("goal")

with st.form("intake_form"):
    metric = st.radio(
        "1 · Primary outcome — which number is this campaign judged on?",
        _options,
        index=_options.index(_cur_metric) if _cur_metric in _options else 0,
        captions=[f"{cov[m]['kind']} · data in {cov[m]['weeks_with_data']}/"
                  f"{cov[m]['weeks_total']} weeks" for m in _options])
    label = st.text_input("What does the client call this?", value=_cur_label,
                          help="Used in every chart title and narrative, e.g. "
                               "“application starts”.")

    st.markdown("**2 · Funnel** — which steps matter for this engagement?")
    _candidates = facts.get("funnel_candidates", [])
    _checks = {}
    _fcols = st.columns(min(4, max(1, len(_candidates))))
    for i, c in enumerate(_candidates):
        with _fcols[i % len(_fcols)]:
            _checks[c] = st.checkbox(c.replace("_", " "), value=c in _cur_steps,
                                     key=f"fs_{c}")

    with st.expander("3 · Targets (optional — skipping is a recorded answer)"):
        st.caption("Leave at 0 for “no target yet”: the dashboard then grades "
                   "against the channel spread, labeled as such.")
        t1, t2, t3 = st.columns(3)
        good = t1.number_input("Cost per outcome — good ≤", min_value=0.0,
                               value=float(_cur_cost_band.get("good") or 0.0))
        warn = t2.number_input("Cost per outcome — warn ≤", min_value=0.0,
                               value=float(_cur_cost_band.get("warn") or 0.0))
        goal = t3.number_input("Volume goal for the flight", min_value=0.0,
                               value=float(_cur_goal or 0.0), step=1000.0)
        b1, b2 = st.columns(2)
        b_total = b1.number_input("Budget — total planned spend", min_value=0.0,
                                  value=float((res.budget or {}).get("total")
                                              or 0.0), step=1000.0)
        b_weeks = b2.number_input("Budget — flight weeks", min_value=0.0,
                                  value=float((res.budget or {}).get("flight_weeks")
                                              or 0.0), step=1.0)

    n1, n2 = st.columns(2)
    client_name = n1.text_input("4 · Client name",
                                value=res.client_name or "",
                                help="Business identity — never inferred from data.")
    campaign_name = n2.text_input("Campaign name", value=res.campaign_name or "")

    submitted = st.form_submit_button("Confirm framing", type="primary")

if submitted:
    framing: dict = {"kpi_metric": metric, "kpi_label": label.strip(),
                     "funnel_steps": [c for c in _candidates if _checks.get(c)]}
    targets: dict = {}
    cost_key = intake.COST_PER_KEY[metric]
    band = {k: v for k, v in (("good", good), ("warn", warn)) if v > 0}
    if band:
        targets[cost_key] = band
    if goal > 0:
        targets[metric] = {"goal": goal}
    framing["targets"] = targets
    if client_name.strip():
        framing["client_name"] = client_name.strip()
    if campaign_name.strip():
        framing["campaign_name"] = campaign_name.strip()
    if b_total > 0:
        framing["budget"] = {"total": b_total,
                             **({"flight_weeks": b_weeks} if b_weeks > 0 else {})}
    path = write_engagement(ROOT, framing)
    st.success(f"Framing confirmed → `{path.relative_to(ROOT)}`. Every page now "
               "reads it; explicit `config.yaml` keys still override.")
    st.rerun()
