"""Generate realistic, messy platform exports for a federal-agency RECRUITMENT scenario.

Scenario: a federal agency running recruitment marketing (~$400k over ~4 months / 17
weeks) across Google Ads (Search brand/nonbrand + Demand Gen awareness/consideration),
Meta (prospecting / engagement / retargeting) and LinkedIn (awareness / lead gen /
retargeting). The KPI is the GA4 key event ``start_application`` — the tracked
"start application" CTA on the AGENCY'S OWN careers site.

This is a real-world test, so two deliberate honesties:
  * Export FORMATS reproduce exactly how each platform exports (title rows, quoted
    thousands, locale dates, `#` preambles, totals rows) — the readers must survive real
    files, so matching real formats is fidelity, not cheating.
  * Ad-set / creative NAMES are written the way a real ad-ops team names things — mixed
    delimiters, free text, inconsistent casing — and are NOT reverse-engineered against
    the decoder. Whatever the decoder parses, parses; the rest lands in "(unparsed)".
    The unparsed rate is a genuine, un-tuned outcome, not a target.

Only a NUMERIC reconciliation key is written (`_reconciliation.json`): per-channel /
-campaign / -ad-group spend, clicks, true vs platform-CLAIMED conversions. It lets us
confirm the messy-format parsing didn't drop or mangle numbers — the parser never sees
it. There is deliberately NO "intended decode per name" answer key.

Deterministic (seeded). Writes to ``--out`` (default data/inbox/).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

START, DAYS = "2026-02-02", 119            # 17 weeks, ~4 months (through 2026-05-31)
SCALE = 1.087                              # lands total delivered spend at ~$400k
# video-view rate per channel (mid-funnel engagement; Search has no video inventory)
_VTR = {"google_search": 0.0, "google_demandgen": 0.20, "meta": 0.18, "linkedin": 0.12}
ACCOUNT = "Federal Agency - Talent Acquisition (500987654)"
GA4_GAP = ("2026-03-16", "2026-03-19")     # simulated site-tag outage (data missing)

# campaign: (channel, daily budget $, CPC $, CTR, true CVR click->start_application,
#            incrementality of those conversions, platform over-claim factor)
CAMPAIGNS = {
    "Search - Brand":                       ("google_search", 185.0, 2.40, 0.065, 0.110, 0.30, 1.05),
    "Search - NonBrand (Cyber & Data)":     ("google_search", 655.0, 6.50, 0.035, 0.045, 0.85, 1.10),
    "DemandGen - Awareness":                ("google_demandgen", 319.0, 1.70, 0.008, 0.006, 0.92, 1.45),
    "DemandGen - Consideration":            ("google_demandgen", 235.0, 2.10, 0.013, 0.018, 0.80, 1.30),
    "Meta - Prospecting":                   ("meta", 504.0, 2.20, 0.011, 0.012, 0.90, 1.60),
    "Meta - Engagement":                    ("meta", 286.0, 1.60, 0.019, 0.020, 0.75, 1.50),
    "Meta - Retargeting":                   ("meta", 235.0, 1.50, 0.023, 0.050, 0.55, 1.80),
    "LinkedIn - Awareness (Professionals)": ("linkedin", 487.0, 9.20, 0.006, 0.020, 0.90, 1.25),
    "LinkedIn - Lead Gen (Explore Roles)":  ("linkedin", 252.0, 7.80, 0.009, 0.035, 0.80, 1.35),
    "LinkedIn - Retargeting (Site Visitors)": ("linkedin", 202.0, 6.90, 0.012, 0.060, 0.60, 1.40),
}

CAMPAIGN_IDS = {name: 20_500_000_000 + i * 131_313 for i, name in enumerate(CAMPAIGNS)}

# campaign -> [(ad-set / creative name, spend share)]. Names are written as a real
# recruitment ad-ops team would — NOT matched to the decoder. Some happen to follow a
# convention, most don't; the decoder files whatever it can't parse under "(unparsed)".
AD_GROUPS = {
    "Search - Brand": [
        ("Brand - Exact", 1.0),
    ],
    "Search - NonBrand (Cyber & Data)": [
        ("Cyber Jobs - Broad", 0.35),
        ("Data Analyst Jobs_Phrase", 0.35),
        ("federal careers nonbrand (legacy)", 0.30),
    ],
    "DemandGen - Awareness": [
        ("InMarket Gov Jobs", 0.5),
        ("Lookalike - Applicants", 0.5),
    ],
    "DemandGen - Consideration": [
        ("Explore Roles - Cyber", 0.5),
        ("Explore Roles - Data", 0.5),
    ],
    "Meta - Prospecting": [
        ("Veterans - Feed", 0.40),
        ("LAL 1% Applicants", 0.35),
        ("Advantage+ broad TEST", 0.25),
    ],
    "Meta - Engagement": [
        ("Life at the Agency // Reels", 0.5),
        ("Role Explainer - Feed", 0.5),
    ],
    "Meta - Retargeting": [
        ("RT SiteVisitors 30d", 0.6),
        ("RT_Careers_Viewers_Reels", 0.4),
    ],
    "LinkedIn - Awareness (Professionals)": [
        ("MissionHero video 1x1", 0.55),
        ("Testimonial_Static_1x1_v1", 0.45),
    ],
    "LinkedIn - Lead Gen (Explore Roles)": [
        ("Role Spotlight - Carousel", 0.55),
        ("benefits one-pager (old)", 0.45),
    ],
    "LinkedIn - Retargeting (Site Visitors)": [
        ("Deadline push STATIC v3", 0.6),
        ("MissionHero_VID_1x1_V2", 0.4),
    ],
}


def _simulate(rng):
    dates = pd.date_range(START, periods=DAYS, freq="D")
    dow = dates.dayofweek.to_numpy()
    rows, truth = [], {}
    for name, (ch, base, cpc, ctr, cvr, incr, claim) in CAMPAIGNS.items():
        wk_dip = 0.35 if ch == "linkedin" else 0.7 if ch == "google_search" else 0.85
        weekday = np.where(dow >= 5, wk_dip, 1.0)
        ramp = np.linspace(0.85, 1.15, DAYS)
        spend = base * SCALE * weekday * ramp * rng.lognormal(0.0, 0.18, DAYS)
        clicks = np.round(spend / cpc * rng.lognormal(0.0, 0.10, DAYS))
        impressions = np.round(clicks / ctr * rng.lognormal(0.0, 0.08, DAYS))
        true_conv = rng.binomial(np.maximum(clicks, 0).astype(int), cvr).astype(float)
        platform_conv = true_conv * claim * rng.lognormal(0.0, 0.05, DAYS)
        video_views = np.round(impressions * _VTR[ch] * rng.lognormal(0.0, 0.15, DAYS))
        rows.append(pd.DataFrame({
            "date": dates, "campaign": name, "channel": ch, "spend": spend.round(2),
            "impressions": impressions, "clicks": clicks, "video_views": video_views,
            "true_conv": true_conv, "platform_conv": platform_conv.round(1)}))
        truth[name] = {
            "channel": ch, "total_spend": float(spend.sum().round(2)),
            "true_start_applications": float(true_conv.sum()),
            "incremental_start_applications": float((true_conv * incr).sum().round(1)),
            "platform_claimed_conversions": float(platform_conv.sum().round(1)),
        }
    return pd.concat(rows, ignore_index=True), truth


def _alloc_ints(total: np.ndarray, shares: np.ndarray) -> np.ndarray:
    raw = shares * total
    base = np.floor(raw)
    frac = raw - base
    rem = np.rint(total - base.sum(axis=0)).astype(int)
    order = np.argsort(-frac, axis=0)
    for j in range(raw.shape[1]):
        base[order[: rem[j], j], j] += 1
    return base


def _split_ad_groups(df: pd.DataFrame, rng) -> pd.DataFrame:
    """Decompose each campaign's daily numbers across its ad groups, exactly (reconciles)."""
    out = []
    for name, g in df.groupby("campaign", sort=False):
        groups = AD_GROUPS[name]
        w = np.array([wt for _, wt in groups])[:, None]
        shares = w * rng.lognormal(0.0, 0.12, (len(groups), len(g)))
        shares /= shares.sum(axis=0, keepdims=True)

        spend = np.round(g["spend"].to_numpy() * shares, 2)
        spend[-1] = np.round(g["spend"].to_numpy() - spend[:-1].sum(axis=0), 2)
        imps = _alloc_ints(g["impressions"].to_numpy(), shares)
        clicks = _alloc_ints(g["clicks"].to_numpy(), shares)
        vids = _alloc_ints(g["video_views"].to_numpy(), shares)
        conv = g["platform_conv"].to_numpy() * shares

        for i, (gname, _wt) in enumerate(groups):
            out.append(pd.DataFrame({
                "date": g["date"].to_numpy(), "campaign": name,
                "channel": g["channel"].to_numpy(), "ad_group": gname,
                "spend": spend[i], "impressions": imps[i], "clicks": clicks[i],
                "video_views": vids[i], "platform_conv": conv[i].round(1)}))
    return pd.concat(out, ignore_index=True)


def _fmt_thousands(x) -> str:
    return f"{x:,.0f}"


def write_google(df, INBOX) -> None:
    g = df[df["channel"].str.startswith("google")].copy()
    ctype = np.where(g["channel"] == "google_search", "Search", "Demand Gen")
    out = pd.DataFrame({
        "Day": g["date"].dt.strftime("%Y-%m-%d"),
        "Campaign": g["campaign"],
        "Campaign ID": g["campaign"].map(CAMPAIGN_IDS),
        "Campaign type": ctype,
        "Currency code": "USD",
        "Cost": g["spend"].map(lambda x: f"{x:.2f}"),
        "Impr.": g["impressions"].map(_fmt_thousands),
        "Clicks": g["clicks"].map(_fmt_thousands),
        "Conversions": g["platform_conv"].map(lambda x: f"{x:.1f}"),
        "Conv. value": "0.00",
        "Video views": g["video_views"].map(_fmt_thousands),
    }).sort_values(["Day", "Campaign"])
    total = (f'Total: Campaigns,,,,USD,"{g["spend"].sum():,.2f}",'
             f'"{g["impressions"].sum():,.0f}","{g["clicks"].sum():,.0f}",'
             f'"{g["platform_conv"].sum():,.1f}",0.00,"{g["video_views"].sum():,.0f}"')
    text = ('Campaign report\n"February 2, 2026 - May 31, 2026"\n'
            + out.to_csv(index=False) + total + "\n")
    (INBOX / "Campaign report.csv").write_text(text, encoding="utf-8")


def write_google_adgroups(ad, INBOX) -> None:
    g = ad[ad["channel"].str.startswith("google")].copy()
    ctype = np.where(g["channel"] == "google_search", "Search", "Demand Gen")
    out = pd.DataFrame({
        "Day": g["date"].dt.strftime("%Y-%m-%d"),
        "Campaign": g["campaign"],
        "Campaign ID": g["campaign"].map(CAMPAIGN_IDS),
        "Ad group": g["ad_group"],
        "Campaign type": ctype,
        "Currency code": "USD",
        "Cost": g["spend"].map(lambda x: f"{x:.2f}"),
        "Impr.": g["impressions"].map(_fmt_thousands),
        "Clicks": g["clicks"].map(_fmt_thousands),
        "Conversions": g["platform_conv"].map(lambda x: f"{x:.1f}"),
        "Conv. value": "0.00",
        "Video views": g["video_views"].map(_fmt_thousands),
    }).sort_values(["Day", "Campaign", "Ad group"])
    total = (f'Total: Ad groups,,,,,USD,"{g["spend"].sum():,.2f}",'
             f'"{g["impressions"].sum():,.0f}","{g["clicks"].sum():,.0f}",'
             f'"{g["platform_conv"].sum():,.1f}",0.00,"{g["video_views"].sum():,.0f}"')
    text = ('Ad group report\n"February 2, 2026 - May 31, 2026"\n'
            + out.to_csv(index=False) + total + "\n")
    (INBOX / "Ad group report.csv").write_text(text, encoding="utf-8")


def write_meta(df, INBOX) -> None:
    m = df[df["channel"] == "meta"]
    out = pd.DataFrame({
        "Day": m["date"].dt.strftime("%Y-%m-%d"),
        "Campaign name": m["campaign"],
        "Amount spent (USD)": m["spend"].map(lambda x: f"{x:.2f}"),
        "Impressions": m["impressions"].astype(int),
        "Link clicks": m["clicks"].astype(int),
        "Video plays": m["video_views"].astype(int),
        "Results": m["platform_conv"].round(0).astype(int),
        "Result indicator": "actions:offsite_conversion.fb_pixel_custom.StartApplication",
    }).sort_values(["Day", "Campaign name"])
    out.to_csv(INBOX / "Meta Ads Manager - Campaigns.csv", index=False)


def write_meta_adsets(ad, INBOX) -> None:
    m = ad[ad["channel"] == "meta"]
    out = pd.DataFrame({
        "Day": m["date"].dt.strftime("%Y-%m-%d"),
        "Campaign name": m["campaign"],
        "Ad set name": m["ad_group"],
        "Amount spent (USD)": m["spend"].map(lambda x: f"{x:.2f}"),
        "Impressions": m["impressions"].astype(int),
        "Link clicks": m["clicks"].astype(int),
        "Video plays": m["video_views"].astype(int),
        "Results": m["platform_conv"].round(0).astype(int),
        "Result indicator": "actions:offsite_conversion.fb_pixel_custom.StartApplication",
    }).sort_values(["Day", "Campaign name", "Ad set name"])
    out.to_csv(INBOX / "Meta Ads Manager - Ad Sets.csv", index=False)


def _linkedin_preamble() -> str:
    return ("Campaign Performance Report\n"
            "Date Range: 2/2/2026 - 5/31/2026\n"
            f"Account: {ACCOUNT}\n"
            "Currency: USD\n\n")


def write_linkedin(df, INBOX) -> None:
    li = df[df["channel"] == "linkedin"]
    out = pd.DataFrame({
        "Start Date (in UTC)": [f"{d.month}/{d.day}/{d.year}" for d in li["date"]],
        "Campaign Name": li["campaign"],
        "Campaign ID": li["campaign"].map(CAMPAIGN_IDS),
        "Total Spent": li["spend"].map(lambda x: f"{x:.2f}"),
        "Impressions": li["impressions"].astype(int),
        "Clicks": li["clicks"].astype(int),
        "Video Views": li["video_views"].astype(int),
        "Conversions": li["platform_conv"].round(0).astype(int),
        "Leads": 0,
    }).sort_values(["Campaign Name"])
    (INBOX / "LinkedIn Campaign Performance.csv").write_text(
        _linkedin_preamble() + out.to_csv(index=False), encoding="utf-8")


def write_linkedin_creatives(ad, INBOX) -> None:
    li = ad[ad["channel"] == "linkedin"]
    out = pd.DataFrame({
        "Start Date (in UTC)": [f"{d.month}/{d.day}/{d.year}" for d in li["date"]],
        "Campaign Name": li["campaign"],
        "Campaign ID": li["campaign"].map(CAMPAIGN_IDS),
        "Creative Name": li["ad_group"],
        "Total Spent": li["spend"].map(lambda x: f"{x:.2f}"),
        "Impressions": li["impressions"].astype(int),
        "Clicks": li["clicks"].astype(int),
        "Video Views": li["video_views"].astype(int),
        "Conversions": li["platform_conv"].round(0).astype(int),
    }).sort_values(["Campaign Name", "Creative Name"])
    preamble = _linkedin_preamble().replace("Campaign Performance Report",
                                            "Creative Performance Report")
    (INBOX / "LinkedIn Creative Performance.csv").write_text(
        preamble + out.to_csv(index=False), encoding="utf-8")


def write_ga4(df, INBOX, rng) -> None:
    smap = {"google_search": "google / cpc", "google_demandgen": "google / cpc",
            "meta": "facebook / paid_social", "linkedin": "linkedin / paid_social"}
    paid = df.copy()
    capture = rng.binomial(paid["true_conv"].astype(int), 0.92).astype(float)
    g = pd.DataFrame({
        "Date": paid["date"].dt.strftime("%Y%m%d"),
        "Session source / medium": paid["channel"].map(smap),
        "Session campaign": paid["campaign"],
        "Sessions": (paid["clicks"] * rng.uniform(0.82, 0.95, len(paid))).astype(int),
        "Engaged sessions": (paid["clicks"] * rng.uniform(0.45, 0.65, len(paid))).astype(int),
        "Key events": capture.astype(int),
    })
    g["Views"] = (g["Sessions"] * rng.uniform(2.0, 4.0, len(g))).astype(int)   # page views
    dates = pd.date_range(START, periods=DAYS, freq="D")
    org_rows = []
    for src, base_sessions, base_keyev in [("google / organic", 240, 3.0),
                                           ("(direct) / (none)", 150, 2.0)]:
        t = np.arange(DAYS)
        season = 1.0 + 0.25 * np.sin(2 * np.pi * (t + 20) / 182.0)
        sess = (base_sessions * season * rng.lognormal(0, 0.15, DAYS)).astype(int)
        kev = rng.poisson(base_keyev * season)
        org_rows.append(pd.DataFrame({
            "Date": dates.strftime("%Y%m%d"), "Session source / medium": src,
            "Session campaign": "(not set)", "Sessions": sess,
            "Engaged sessions": (sess * 0.5).astype(int),
            "Key events": kev,
            "Views": (sess * rng.uniform(2.0, 3.5, DAYS)).astype(int)}))
    out = pd.concat([g] + org_rows, ignore_index=True)
    # simulated tracking outage: GA4 rows go missing for a few days (real-world quirk)
    gap = (out["Date"] >= GA4_GAP[0].replace("-", "")) & (out["Date"] <= GA4_GAP[1].replace("-", ""))
    out = out[~gap].sort_values(["Date", "Session source / medium", "Session campaign"])
    preamble = ("# All Users\n"
                "# Traffic acquisition: Session source/medium\n"
                f"# {pd.Timestamp(START):%Y%m%d}-{(pd.Timestamp(START) + pd.Timedelta(days=DAYS - 1)):%Y%m%d}\n")
    (INBOX / "GA4 Traffic acquisition.csv").write_text(
        preamble + out.to_csv(index=False), encoding="utf-8")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Generate messy federal-recruitment exports.")
    ap.add_argument("--out", default=str(ROOT / "data" / "inbox"),
                    help="output folder (default data/inbox/)")
    args = ap.parse_args(argv)
    INBOX = Path(args.out)
    INBOX.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(11)
    df, truth = _simulate(rng)
    write_google(df, INBOX)
    write_meta(df, INBOX)
    write_linkedin(df, INBOX)
    write_ga4(df, INBOX, rng)

    ad = _split_ad_groups(df, rng)
    write_google_adgroups(ad, INBOX)
    write_meta_adsets(ad, INBOX)
    write_linkedin_creatives(ad, INBOX)

    # NUMERIC reconciliation only (no decode answer key)
    by_ad_group = {
        camp: {
            gname: {
                "spend": float(ad[(ad["campaign"] == camp) & (ad["ad_group"] == gname)]["spend"].sum().round(2)),
                "platform_claimed_conversions":
                    float(ad[(ad["campaign"] == camp) & (ad["ad_group"] == gname)]["platform_conv"].sum().round(1)),
            }
            for gname, _wt in groups
        }
        for camp, groups in AD_GROUPS.items()
    }
    by_channel: dict = {}
    for name, t in truth.items():
        c = by_channel.setdefault(t["channel"], {"total_spend": 0.0,
                                                 "true_start_applications": 0.0,
                                                 "incremental_start_applications": 0.0,
                                                 "platform_claimed_conversions": 0.0})
        for k in c:
            c[k] = round(c[k] + t[k], 1)
    json.dump({"scenario": "federal agency recruitment, 17 weeks (~$400k)",
               "kpi": "GA4 key event start_application (agency careers site)",
               "note": "NUMERIC reconciliation only — no per-name decode key by design",
               "by_campaign": truth, "by_channel": by_channel, "by_ad_group": by_ad_group},
              open(INBOX / "_reconciliation.json", "w", encoding="utf-8"), indent=2)

    (INBOX / "README.md").write_text(f"""# Federal agency recruitment — platform exports (synthetic)

Simulated downloads from Google Ads, Meta, LinkedIn and GA4 for a federal agency's
recruitment campaign (~$400k / 17 weeks). KPI = GA4 key event `start_application`
(the "start application" CTA on the agency's careers site). Public sector -> no revenue.

## Files (move the 7 CSVs into `data/inbox/`)
| file | platform / grain |
|---|---|
| Campaign report.csv | Google Ads, campaign |
| Ad group report.csv | Google Ads, ad group |
| Meta Ads Manager - Campaigns.csv | Meta, campaign |
| Meta Ads Manager - Ad Sets.csv | Meta, ad set |
| LinkedIn Campaign Performance.csv | LinkedIn, campaign |
| LinkedIn Creative Performance.csv | LinkedIn, creative |
| GA4 Traffic acquisition.csv | GA4, session source/medium x campaign |

`_reconciliation.json` is a NUMERIC answer key (spend/clicks/true-vs-claimed conversions)
so parsing fidelity can be checked. There is NO per-name decode key: ad-set / creative
names are written the way a real ad-ops team names things and are not tuned to the
decoder — whatever doesn't follow a convention lands honestly in `(unparsed)`.

Each export also carries mid-funnel engagement: video views (Meta "Video plays",
LinkedIn "Video Views", Google Demand Gen "Video views") and GA4 page views ("Views").

Known real-world messiness the pipeline must survive: Google title rows + quoted
thousands + `Total:` row; LinkedIn 4-line preamble + M/D/YYYY dates; GA4 `#` preamble +
YYYYMMDD + organic/(direct) rows + a {GA4_GAP[0]}–{GA4_GAP[1]} tracking gap (missing days);
inconsistent ad-set / creative names (fixed via the naming crosswalk, below).

## Run it
```
python scripts/ingest.py --inbox --reset       # parse -> canonical -> history.parquet
python scripts/naming_report.py                # (optional) what's still unparsed + draft fixes
python scripts/run_pipeline.py                 # clean -> weekly metrics + data quality
streamlit run src/advanced_reporting/dashboard/app.py
```
Inconsistent ad names are remediated by `config/naming_overrides.yaml` (a curated
raw-name -> audience/creative crosswalk, applied at ingest). It's pre-filled for this
data; `scripts/naming_report.py` drafts suggestions for any names still unparsed.
""", encoding="utf-8")

    print(f"Wrote 7 exports + _reconciliation.json + README.md to {INBOX}")
    for name, t in by_channel.items():
        print(f"  {name:<18} spend ${t['total_spend']:>10,.0f}  "
              f"true start-apps {t['true_start_applications']:>6,.0f}  "
              f"platform claims {t['platform_claimed_conversions']:>7,.0f}")
    print(f"  total spend ${sum(t['total_spend'] for t in by_channel.values()):,.0f}")


if __name__ == "__main__":
    sys.exit(main())
