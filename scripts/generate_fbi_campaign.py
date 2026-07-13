"""Emit the FBI recruiting engagement as realistic platform export files.

Runs the scenario DGP (``ingestion/scenario_dgp.py``) and writes a self-contained dataset
folder ``data/MMM Data/`` (gitignored) that ``scripts/ingest.py --inbox "data/MMM Data"
--reset`` reads:

  - ``Ad group report.csv``            Google Ads ad-group export: SEARCH + YOUTUBE (video)
  - ``Meta Ads Manager - Ad Sets.csv`` Meta ad-set export
  - ``LinkedIn Creative Performance.csv`` LinkedIn creative export
  - ``GA4 Traffic acquisition.csv``    GA4 sessions / key events (all channels + organic)
  - ``campaign_delivery.csv``          display / ctv / audio / jobboards at campaign grain
                                       (generic csv — mappings.yaml only, no new reader)
  - ``business_kpi_weekly.csv``        the MMM target: week x geo submitted apps + controls
  - ``crm_pipeline_stages.csv``        the post-submission applicant pipeline (6 stages)
  - ``ground_truth.json``              per-channel true contribution + ROI + the identity
  - ``README.md``                      the scenario, the files, how to ingest

Every ad-level export carries a ``Region`` (US-* geo) column so the store's geo x weekly
table is complete (synthetic liberty; real platforms expose geo segments). Daily grain is
synthesised from the DGP's weekly values with a weekday delivery profile.

Deterministic (seeded via the scenario). ``--mini`` (16 weeks x 3 geos) is the fast preset
used by tests; the full run is manual/local.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from advanced_reporting.ingestion import scenario, scenario_dgp  # noqa: E402

# weekday delivery profile (Mon..Sun): recruiting skews to weekdays
_DOW = np.array([1.08, 1.10, 1.10, 1.06, 0.98, 0.84, 0.84])
_ADDITIVE = ("spend", "impressions", "clicks", "sessions", "engaged_sessions",
             "key_events", "conversions", "video_views", "starts_true")


def _to_daily(weekly: pd.DataFrame, rng) -> pd.DataFrame:
    """Explode weekly rows to 7 daily rows each, splitting additive metrics by a weekday
    profile (spend reconciles exactly; counts are rounded)."""
    n = len(weekly)
    w = _DOW / _DOW.sum()
    # per-row daily weights with mild noise (n x 7)
    noise = rng.lognormal(0.0, 0.05, (n, 7))
    dw = w[None, :] * noise
    dw = dw / dw.sum(axis=1, keepdims=True)

    base = weekly.loc[weekly.index.repeat(7)].reset_index(drop=True)
    day_offset = np.tile(np.arange(7), n)
    base["date"] = pd.to_datetime(base["date"]) + pd.to_timedelta(day_offset, unit="D")
    flat = dw.reshape(-1)
    for col in _ADDITIVE:
        if col in base.columns:
            base[col] = base[col].to_numpy() * flat
    # round counts; keep spend to cents
    base["spend"] = base["spend"].round(2)
    for c in ("impressions", "clicks", "sessions", "engaged_sessions", "key_events",
              "conversions", "video_views"):
        if c in base.columns:
            base[c] = base[c].round().astype(int)
    return base


def _fmt_k(x) -> str:
    return f"{x:,.0f}"


# ------------------------------------------------------------------ ad exports
def write_google_adgroups(daily, out: Path) -> None:
    """Google Ads ad-group export covering SEARCH and YOUTUBE (video) campaigns."""
    g = daily[daily["channel"].isin(["google_search", "youtube"])].copy()
    if g.empty:
        return
    ctype = np.where(g["channel"] == "google_search", "Search", "Video")
    df = pd.DataFrame({
        "Day": g["date"].dt.strftime("%Y-%m-%d"),
        "Campaign": g["campaign"], "Campaign ID": g["campaign"].map(_cid),
        "Ad group": g["ad_group"], "Region": g["geo"], "Campaign type": ctype,
        "Currency code": "USD",
        "Cost": g["spend"].map(lambda x: f"{x:.2f}"),
        "Impr.": g["impressions"].map(_fmt_k), "Clicks": g["clicks"].map(_fmt_k),
        "Conversions": g["conversions"].map(lambda x: f"{x:.1f}"),
        "Conv. value": "0.00", "Video views": g["video_views"].map(_fmt_k),
    }).sort_values(["Day", "Campaign", "Ad group", "Region"])
    total = (f'Total: Ad groups,,,,,,USD,"{g["spend"].sum():,.2f}",'
             f'"{g["impressions"].sum():,.0f}","{g["clicks"].sum():,.0f}",'
             f'"{g["conversions"].sum():,.1f}",0.00,"{g["video_views"].sum():,.0f}"')
    text = ("Ad group report\n" + _daterange(g) + df.to_csv(index=False) + total + "\n")
    (out / "Ad group report.csv").write_text(text, encoding="utf-8")


def write_meta_adsets(daily, out: Path) -> None:
    m = daily[daily["channel"] == "meta"].copy()
    if m.empty:
        return
    df = pd.DataFrame({
        "Day": m["date"].dt.strftime("%Y-%m-%d"),
        "Campaign name": m["campaign"], "Ad set name": m["ad_group"], "Region": m["geo"],
        "Amount spent (USD)": m["spend"].map(lambda x: f"{x:.2f}"),
        "Impressions": m["impressions"].astype(int), "Link clicks": m["clicks"].astype(int),
        "Video plays": m["video_views"].astype(int),
        "Results": m["conversions"].round(0).astype(int),
        "Result indicator": "actions:offsite_conversion.fb_pixel_custom.StartApplication",
    }).sort_values(["Day", "Campaign name", "Ad set name", "Region"])
    df.to_csv(out / "Meta Ads Manager - Ad Sets.csv", index=False)


def write_linkedin_creatives(daily, out: Path) -> None:
    li = daily[daily["channel"] == "linkedin"].copy()
    if li.empty:
        return
    df = pd.DataFrame({
        "Start Date (in UTC)": [f"{d.month}/{d.day}/{d.year}" for d in li["date"]],
        "Campaign Name": li["campaign"], "Campaign ID": li["campaign"].map(_cid),
        "Creative Name": li["ad_group"], "Region": li["geo"],
        "Total Spent": li["spend"].map(lambda x: f"{x:.2f}"),
        "Impressions": li["impressions"].astype(int), "Clicks": li["clicks"].astype(int),
        "Video Views": li["video_views"].astype(int),
        "Conversions": li["conversions"].round(0).astype(int),
    }).sort_values(["Campaign Name", "Creative Name", "Region"])
    preamble = ("Creative Performance Report\nDate Range: "
                + _li_daterange(li) + "\nAccount: Federal Bureau of Investigation - "
                "Talent Acquisition (500112233)\nCurrency: USD\n\n")
    (out / "LinkedIn Creative Performance.csv").write_text(
        preamble + df.to_csv(index=False), encoding="utf-8")


def write_generic_campaign(daily, out: Path) -> None:
    """display / ctv / audio / jobboards at campaign grain — a plain, mappings-only CSV."""
    c = daily[daily["channel"].isin(["display", "ctv", "audio", "jobboards"])].copy()
    if c.empty:
        return
    df = pd.DataFrame({
        "date": c["date"].dt.strftime("%Y-%m-%d"), "channel": c["channel"],
        "campaign": c["campaign"], "geo": c["geo"], "currency": "USD",
        "spend": c["spend"].round(2), "impressions": c["impressions"].astype(int),
        "clicks": c["clicks"].astype(int),
        "conversions": c["conversions"].round(1),
    }).sort_values(["date", "channel", "campaign", "geo"])
    df.to_csv(out / "campaign_delivery.csv", index=False)


def write_ga4(daily, out: Path) -> None:
    """GA4 traffic-acquisition export: sessions / engaged / key events per channel x geo."""
    smap = {"google_search": "google / cpc", "youtube": "youtube / cpc",
            "meta": "facebook / paid_social", "linkedin": "linkedin / paid_social",
            "display": "google / display", "ctv": "ctv / video", "audio": "audio / audio",
            "jobboards": "jobboards / referral", "organic_search": "google / organic",
            "direct": "(direct) / (none)", "email": "email / newsletter",
            "social_organic": "social / organic"}
    g = daily[daily["key_events"].notna()].copy()
    g = g[g["sessions"] > 0]
    df = pd.DataFrame({
        "Date": g["date"].dt.strftime("%Y%m%d"),
        "Session source / medium": g["channel"].map(smap),
        "Session campaign": g["campaign"].where(g["campaign"] != "(organic)", "(not set)"),
        "Region": g["geo"],
        "Sessions": g["sessions"].astype(int),
        "Engaged sessions": g["engaged_sessions"].astype(int),
        "Key events": g["key_events"].astype(int),
        "Views": (g["sessions"] * 3).astype(int),
    }).sort_values(["Date", "Session source / medium", "Session campaign", "Region"])
    preamble = ("# All Users\n# Traffic acquisition: Session source/medium\n"
                f"# {g['date'].min():%Y%m%d}-{g['date'].max():%Y%m%d}\n")
    (out / "GA4 Traffic acquisition.csv").write_text(
        preamble + df.to_csv(index=False), encoding="utf-8")


# ------------------------------------------------------------------ CRM + truth
def write_business_kpi(data: scenario_dgp.ScenarioData, out: Path) -> None:
    """The MMM target: week x geo submitted applications + the control columns."""
    kpi = data.kpi_weekly.copy()
    kpi["date"] = pd.to_datetime(kpi["date"]).dt.strftime("%Y-%m-%d")
    kpi["submitted_applications"] = kpi["submitted_applications"].round().astype(int)
    kpi.to_csv(out / "business_kpi_weekly.csv", index=False)


def write_pipeline(data: scenario_dgp.ScenarioData, out: Path) -> None:
    pl = data.pipeline_stages.copy()
    if len(pl):
        pl["date"] = pd.to_datetime(pl["date"]).dt.strftime("%Y-%m-%d")
        pl["count"] = pl["count"].round().astype(int)
        pl = pl[pl["count"] > 0]
    pl.to_csv(out / "crm_pipeline_stages.csv", index=False)


def _campaign_ids(campaigns) -> dict:
    return {c: 20_600_000_000 + i * 97_531 for i, c in enumerate(sorted(set(campaigns)))}


_CID: dict = {}


def _cid(name):
    return _CID.get(name, 0)


def _daterange(g) -> str:
    lo, hi = g["date"].min(), g["date"].max()
    return f'"{lo.strftime("%B %d, %Y")} - {hi.strftime("%B %d, %Y")}"\n'


def _li_daterange(li) -> str:
    return f"{li['date'].min().month}/{li['date'].min().day}/{li['date'].min().year} - " \
           f"{li['date'].max().month}/{li['date'].max().day}/{li['date'].max().year}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Emit the FBI recruiting engagement.")
    ap.add_argument("--scenario", default="fbi_recruitment")
    ap.add_argument("--out", default=str(ROOT / "data" / "MMM Data"))
    ap.add_argument("--mini", action="store_true", help="16 weeks x 3 geos (fast preset)")
    args = ap.parse_args(argv)

    spec = scenario.load_scenario(args.scenario)
    data = scenario_dgp.generate(spec, mini=args.mini)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    global _CID
    _CID = _campaign_ids(data.media_weekly["campaign"])
    rng = np.random.default_rng(int(spec.get("seed", 0)) + 7)
    daily = _to_daily(data.media_weekly, rng)

    write_google_adgroups(daily, out)
    write_meta_adsets(daily, out)
    write_linkedin_creatives(daily, out)
    write_generic_campaign(daily, out)
    write_ga4(daily, out)
    write_business_kpi(data, out)
    write_pipeline(data, out)

    gt_json = json.dumps(data.ground_truth, indent=2)
    (out / "ground_truth.json").write_text(gt_json, encoding="utf-8")
    # Canonical copy for the MMM validation gate (mmm/validation.py reads outputs/). The
    # dataset folder keeps its own copy so the dataset stays self-contained.
    outputs = ROOT / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "ground_truth.json").write_text(gt_json, encoding="utf-8")
    _write_readme(out, spec, data)

    gt = data.ground_truth["identity"]
    print(f"Wrote FBI dataset -> {out}")
    print(f"  media daily rows {len(daily):,}  |  kpi weeks x geos {len(data.kpi_weekly):,}"
          f"  |  pipeline rows {len(data.pipeline_stages):,}")
    print(f"  identity: baseline {gt['baseline']:,.0f} + paid {gt['paid_contribution']:,.0f}"
          f" = submitted {gt['kpi_submitted']:,.0f}  (paid share {gt['paid_share']:.0%},"
          f" residual {gt['residual']:.2e})")
    _print_realism(spec, data, daily)
    return 0


def _print_realism(spec, data, daily) -> None:
    """The P1 realism sanity table: per-channel CPM/CPC/cost-per-start in the bands."""
    years = len(data.weeks) / 52.0
    m = data.media_weekly
    print("  realism (per-channel):")
    for ch in ("google_search", "youtube", "meta", "linkedin", "ctv", "display",
               "jobboards", "audio"):
        c = m[m["channel"] == ch]
        sp, imp, clk, st = (c["spend"].sum(), c["impressions"].sum(),
                            c["clicks"].sum(), c["starts_true"].sum())
        cpm = sp / imp * 1000 if imp else 0
        cpc = sp / clk if clk else 0
        cps = sp / st if st else 0
        print(f"    {ch:<14} spend ${sp/1e6:5.2f}M  cpm ${cpm:6.1f}  cpc ${cpc:5.2f}  "
              f"cost/start ${cps:5.0f}")
    starts = m["starts_true"].sum()
    sub = data.kpi_weekly["submitted_applications"].sum()
    print(f"    BLENDED cost/start ${m['spend'].sum()/starts:.0f} (target 150-200); "
          f"starts/yr {starts/years:,.0f} (~88k); submitted/yr {sub/years:,.0f} (~37k)")
    _print_sa_pipeline(spec, data, years)


def _print_sa_pipeline(spec, data, years) -> None:
    paths = spec["pipeline"]["paths"]
    pr = paths["SA"]["pass_rate"]
    cum = 1.0
    for s in spec["pipeline"]["stages"]:
        cum *= pr[s]
    sa_sub = 0.45 * data.kpi_weekly["submitted_applications"].sum()
    print(f"    SA pipeline cumulative {cum:.1%} (target ~7%) -> "
          f"~{sa_sub*cum/years:,.0f} BFTC/yr (target ~1,100)")


def _write_readme(out, spec, data) -> None:
    gt = data.ground_truth["identity"]
    (out / "README.md").write_text(f"""# FBI Talent Acquisition — synthetic engagement (MMM Data)

**Fictional.** A continuous FY24–FY26 FBI recruiting program (~$15M/yr paid media)
across 8 paid channels x {len(data.geos)} field-office regions. Applications happen on
the Bureau's own careers portal (fbijobs-style, NOT USAJOBS). The MMM target is
**submitted applications** (a count); the post-submission applicant pipeline is a
reporting layer, never a modeling target ("media buys applications; it cannot pass a
polygraph").

Generated by `scripts/generate_fbi_campaign.py` from `config/scenarios/{spec['name']}.yaml`.
Ground truth in `ground_truth.json` — the accounting identity holds exactly:
baseline {gt['baseline']:,.0f} + paid {gt['paid_contribution']:,.0f} =
submitted {gt['kpi_submitted']:,.0f} (paid share {gt['paid_share']:.0%}).

## Files
| file | platform / grain |
|---|---|
| Ad group report.csv | Google Ads ad-group (Search + YouTube video) |
| Meta Ads Manager - Ad Sets.csv | Meta ad-set |
| LinkedIn Creative Performance.csv | LinkedIn creative |
| GA4 Traffic acquisition.csv | GA4 session source/medium x campaign (mid-funnel) |
| campaign_delivery.csv | display / ctv / audio / jobboards (campaign grain, generic csv) |
| business_kpi_weekly.csv | MMM target: week x geo submitted applications + controls |
| crm_pipeline_stages.csv | post-submission applicant pipeline (6 stages, censored) |
| ground_truth.json | per-channel true contribution + ROI + the identity |

Ad-level exports carry a `Region` (US-* geo) column so the geo x weekly modeling table is
complete. Baked-in MMM stress cases: collinear meta+youtube, a LinkedIn dark stretch,
National-Recruiting-Week bursts, google_search saturation, unproven audio, ctv geo-lever
launch, and ctv's zero-click extreme claim (verified by ~none in GA4).

## Ingest
```
python scripts/ingest.py --inbox "data/MMM Data" --reset
python scripts/run_pipeline.py
```
""", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
