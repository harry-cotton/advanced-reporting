"""Generate realistic platform-export files for the file-drop ingestion milestone.

Scenario: a university marketing its MBA program, ~$100k over 6 months across
Google Ads (Search brand/nonbrand + Demand Gen), Meta (prospecting + retargeting)
and LinkedIn (working professionals + retargeting). The KPI is the GA4 key event
``start_application``; CRM matchback comes later.

Writes to ``data/inbox/`` (gitignored — the drop folder real exports will land in):
  google_ads_campaign_report.csv   Google Ads UI export: 2 title rows, quoted
                                   thousands separators, a totals row at the bottom
  meta_ads_export.csv              Ads Manager export: clean CSV, verbose headers
  linkedin_campaign_export.csv     Campaign Manager export: 5 preamble lines,
                                   M/D/YYYY dates (locale trap)
  ga4_traffic_acquisition.csv      GA4 UI export: '#' comment preamble, YYYYMMDD
                                   dates, source/medium + campaign, organic rows
  google_ads_adgroup_report.csv    ad-GROUP grain: same quirks + 'Ad group' column
  meta_ads_adset_export.csv        ad-SET grain: 'Ad set name' column
  linkedin_creative_export.csv     creative grain: 'Creative Name' column
  _ground_truth.json               the DGP's true per-channel incremental key
                                   events + per-ad-group truth — the answer key
                                   for mapping/decode validation
  README.md                        what each file is and its known quirks

The ad-level files are exact decompositions of the campaign-level numbers (same DGP,
split across ad sets), so either grain — or both — reconciles to the same truth. Ad-set
/ ad names follow the naming generator's grammar (audience_type_audience_detail_placement
and creative_format_size_version) EXCEPT ~15% deliberately non-conforming names, which
must land in the decoder's "(unparsed)" bucket, never be guessed.

Deliberately messy in the ways real exports are messy — the step-2 mapping layer
must handle these files, not idealized ones. Deterministic (seeded).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "data" / "inbox"

START, DAYS = "2026-01-05", 182            # 26 weeks ending 2026-07-05

# campaign: (channel, daily budget $, CPC $, CTR, true CVR click->start_application,
#            incrementality of those conversions, platform over-claim factor)
CAMPAIGNS = {
    "MBA_Search_Brand":              ("google_search", 49.0, 3.20, 0.055, 0.085, 0.30, 1.05),
    "MBA_Search_NonBrand":           ("google_search", 116.0, 6.80, 0.032, 0.038, 0.85, 1.10),
    "MBA_DemandGen_Prospecting":     ("google_demandgen", 82.0, 1.90, 0.009, 0.012, 0.90, 1.35),
    "MBA_Meta_Prospecting":          ("meta", 99.0, 2.40, 0.011, 0.014, 0.90, 1.60),
    "MBA_Meta_Retargeting":          ("meta", 66.0, 1.60, 0.021, 0.045, 0.55, 1.80),
    "MBA_LI_Working_Professionals":  ("linkedin", 88.0, 9.50, 0.006, 0.030, 0.90, 1.25),
    "MBA_LI_Retargeting_SiteVisitors": ("linkedin", 49.0, 7.20, 0.011, 0.055, 0.60, 1.40),
}

CAMPAIGN_IDS = {name: 20_400_000_000 + i * 111_111 for i, name in enumerate(CAMPAIGNS)}

# campaign -> [(ad group / ad set / creative name, spend share, intended decode)].
# Names follow the naming generator's grammar (Ad Set = TYPE_DETAIL_PLACEMENT,
# Ad/creative = CREATIVE_FORMAT_SIZE_VERSION) except the deliberately non-conforming
# ones (intended decode None) — ~15% of names, exercising the "(unparsed)" bucket.
AD_GROUPS = {
    "MBA_Search_Brand": [
        ("PROSPECT_KW-BRAND_SEARCH", 1.0,
         {"audience_type": "PROSPECT", "audience_detail": "KW-BRAND"}),
    ],
    "MBA_Search_NonBrand": [
        ("PROSPECT_KW-MBA_SEARCH", 0.65,
         {"audience_type": "PROSPECT", "audience_detail": "KW-MBA"}),
        ("MBA Degree - Broad (old)", 0.35, None),                # legacy, non-conforming
    ],
    "MBA_DemandGen_Prospecting": [
        ("PROSPECT_INMARKET-EDU_DISCOVER", 0.6,
         {"audience_type": "PROSPECT", "audience_detail": "INMARKET-EDU"}),
        ("PROSPECT_LAL-VISITORS_DISCOVER", 0.4,
         {"audience_type": "PROSPECT", "audience_detail": "LAL-VISITORS"}),
    ],
    "MBA_Meta_Prospecting": [
        ("PROSPECT_LAL-1PCT_FEED", 0.5,
         {"audience_type": "PROSPECT", "audience_detail": "LAL-1PCT"}),
        ("PROSPECT_INT-GRADEDU_FEED", 0.3,
         {"audience_type": "PROSPECT", "audience_detail": "INT-GRADEDU"}),
        ("Advantage+ broad (test)", 0.2, None),                  # non-conforming
    ],
    "MBA_Meta_Retargeting": [
        ("RETARGET_SITE-90D_FEED", 0.7,
         {"audience_type": "RETARGET", "audience_detail": "SITE-90D"}),
        ("RETARGET_ENGAGE-30D_REELS", 0.3,
         {"audience_type": "RETARGET", "audience_detail": "ENGAGE-30D"}),
    ],
    # LinkedIn's sub-campaign entity is the CREATIVE -> Ad-grammar names decode to
    # creative fields (audience stays campaign-level there, honestly).
    "MBA_LI_Working_Professionals": [
        ("BRANDHERO_VID_1x1_V1", 0.55,
         {"creative": "BRANDHERO", "creative_format": "VID"}),
        ("TESTIMONIAL_STATIC_1x1_V1", 0.45,
         {"creative": "TESTIMONIAL", "creative_format": "STATIC"}),
    ],
    "MBA_LI_Retargeting_SiteVisitors": [
        ("DEADLINE_STATIC_1x1_V2", 0.7,
         {"creative": "DEADLINE", "creative_format": "STATIC"}),
        ("BRANDHERO_VID_1x1_V2", 0.3,
         {"creative": "BRANDHERO", "creative_format": "VID"}),
    ],
}


def _simulate(rng):
    dates = pd.date_range(START, periods=DAYS, freq="D")
    dow = dates.dayofweek.to_numpy()
    rows, truth = [], {}
    for name, (ch, base, cpc, ctr, cvr, incr, claim) in CAMPAIGNS.items():
        # B2B weekday pattern (LinkedIn dips hardest on weekends) + gentle ramp + noise
        wk_dip = 0.35 if ch == "linkedin" else 0.7 if ch == "google_search" else 0.85
        weekday = np.where(dow >= 5, wk_dip, 1.0)
        ramp = np.linspace(0.85, 1.15, DAYS)
        spend = base * weekday * ramp * rng.lognormal(0.0, 0.18, DAYS)
        clicks = np.round(spend / cpc * rng.lognormal(0.0, 0.10, DAYS))
        impressions = np.round(clicks / ctr * rng.lognormal(0.0, 0.08, DAYS))
        true_conv = rng.binomial(np.maximum(clicks, 0).astype(int), cvr).astype(float)
        platform_conv = true_conv * claim * rng.lognormal(0.0, 0.05, DAYS)
        rows.append(pd.DataFrame({
            "date": dates, "campaign": name, "channel": ch, "spend": spend.round(2),
            "impressions": impressions, "clicks": clicks,
            "true_conv": true_conv, "platform_conv": platform_conv.round(1)}))
        truth[name] = {
            "channel": ch, "total_spend": float(spend.sum().round(2)),
            "true_start_applications": float(true_conv.sum()),
            "incremental_start_applications": float((true_conv * incr).sum().round(1)),
            "platform_claimed_conversions": float(platform_conv.sum().round(1)),
        }
    return pd.concat(rows, ignore_index=True), truth


def _alloc_ints(total: np.ndarray, shares: np.ndarray) -> np.ndarray:
    """Split integer daily totals across groups by share, exactly (largest remainder)."""
    raw = shares * total
    base = np.floor(raw)
    frac = raw - base
    rem = np.rint(total - base.sum(axis=0)).astype(int)
    order = np.argsort(-frac, axis=0)
    for j in range(raw.shape[1]):
        base[order[: rem[j], j], j] += 1
    return base


def _split_ad_groups(df: pd.DataFrame, rng) -> pd.DataFrame:
    """Decompose each campaign's daily numbers across its ad groups, exactly.

    Shares wobble day-to-day around the configured weights; spend reconciles to the
    cent (last group takes the rounding remainder) and ints via largest remainder, so
    ad-level files sum back to the campaign-level files' numbers.
    """
    out = []
    for name, g in df.groupby("campaign", sort=False):
        groups = AD_GROUPS[name]
        w = np.array([wt for _, wt, _ in groups])[:, None]
        shares = w * rng.lognormal(0.0, 0.12, (len(groups), len(g)))
        shares /= shares.sum(axis=0, keepdims=True)

        spend = np.round(g["spend"].to_numpy() * shares, 2)
        spend[-1] = np.round(g["spend"].to_numpy() - spend[:-1].sum(axis=0), 2)
        imps = _alloc_ints(g["impressions"].to_numpy(), shares)
        clicks = _alloc_ints(g["clicks"].to_numpy(), shares)
        conv = g["platform_conv"].to_numpy() * shares

        for i, (gname, _wt, _dec) in enumerate(groups):
            out.append(pd.DataFrame({
                "date": g["date"].to_numpy(), "campaign": name,
                "channel": g["channel"].to_numpy(), "ad_group": gname,
                "spend": spend[i], "impressions": imps[i], "clicks": clicks[i],
                "platform_conv": conv[i].round(1)}))
    return pd.concat(out, ignore_index=True)


def _fmt_thousands(x) -> str:
    return f"{x:,.0f}"


def write_google(df: pd.DataFrame) -> None:
    g = df[df["channel"].str.startswith("google")].copy()
    ctype = np.where(g["channel"] == "google_search", "Search", "Demand Gen")
    out = pd.DataFrame({
        "Day": g["date"].dt.strftime("%Y-%m-%d"),
        "Campaign": g["campaign"],
        "Campaign ID": g["campaign"].map(CAMPAIGN_IDS),
        "Campaign type": ctype,
        "Currency code": "USD",
        "Cost": g["spend"].map(lambda x: f"{x:.2f}"),
        "Impr.": g["impressions"].map(_fmt_thousands),      # quoted '1,234' — real quirk
        "Clicks": g["clicks"].map(_fmt_thousands),
        "Conversions": g["platform_conv"].map(lambda x: f"{x:.1f}"),
        "Conv. value": "0.00",
    }).sort_values(["Day", "Campaign"])
    total = (f'Total: Campaigns,,,,USD,"{g["spend"].sum():,.2f}",'
             f'"{g["impressions"].sum():,.0f}","{g["clicks"].sum():,.0f}",'
             f'"{g["platform_conv"].sum():,.1f}",0.00')
    body = out.to_csv(index=False)
    text = ('Campaign report\n"January 5, 2026 - July 5, 2026"\n' + body + total + "\n")
    (INBOX / "google_ads_campaign_report.csv").write_text(text, encoding="utf-8")


def write_meta(df: pd.DataFrame) -> None:
    m = df[df["channel"] == "meta"]
    out = pd.DataFrame({
        "Day": m["date"].dt.strftime("%Y-%m-%d"),
        "Campaign name": m["campaign"],
        "Amount spent (USD)": m["spend"].map(lambda x: f"{x:.2f}"),
        "Impressions": m["impressions"].astype(int),
        "Link clicks": m["clicks"].astype(int),
        "Results": m["platform_conv"].round(0).astype(int),
        "Result indicator": "actions:offsite_conversion.fb_pixel_custom.StartApplication",
    }).sort_values(["Day", "Campaign name"])
    out.to_csv(INBOX / "meta_ads_export.csv", index=False)


def write_linkedin(df: pd.DataFrame) -> None:
    li = df[df["channel"] == "linkedin"]
    out = pd.DataFrame({
        "Start Date (in UTC)": [f"{d.month}/{d.day}/{d.year}" for d in li["date"]],
        "Campaign Name": li["campaign"],
        "Campaign ID": li["campaign"].map(CAMPAIGN_IDS),
        "Total Spent": li["spend"].map(lambda x: f"{x:.2f}"),
        "Impressions": li["impressions"].astype(int),
        "Clicks": li["clicks"].astype(int),
        "Conversions": li["platform_conv"].round(0).astype(int),
        "Leads": 0,
    }).sort_values(["Campaign Name"])
    preamble = ("Campaign Performance Report\n"
                "Date Range: 1/5/2026 - 7/5/2026\n"
                "Account: University Graduate School - MBA (507123456)\n"
                "Currency: USD\n\n")
    (INBOX / "linkedin_campaign_export.csv").write_text(
        preamble + out.to_csv(index=False), encoding="utf-8")


def write_google_adgroups(ad: pd.DataFrame) -> None:
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
        "Impr.": g["impressions"].map(_fmt_thousands),      # quoted '1,234' — real quirk
        "Clicks": g["clicks"].map(_fmt_thousands),
        "Conversions": g["platform_conv"].map(lambda x: f"{x:.1f}"),
        "Conv. value": "0.00",
    }).sort_values(["Day", "Campaign", "Ad group"])
    total = (f'Total: Ad groups,,,,,USD,"{g["spend"].sum():,.2f}",'
             f'"{g["impressions"].sum():,.0f}","{g["clicks"].sum():,.0f}",'
             f'"{g["platform_conv"].sum():,.1f}",0.00')
    text = ('Ad group report\n"January 5, 2026 - July 5, 2026"\n'
            + out.to_csv(index=False) + total + "\n")
    (INBOX / "google_ads_adgroup_report.csv").write_text(text, encoding="utf-8")


def write_meta_adsets(ad: pd.DataFrame) -> None:
    m = ad[ad["channel"] == "meta"]
    out = pd.DataFrame({
        "Day": m["date"].dt.strftime("%Y-%m-%d"),
        "Campaign name": m["campaign"],
        "Ad set name": m["ad_group"],
        "Amount spent (USD)": m["spend"].map(lambda x: f"{x:.2f}"),
        "Impressions": m["impressions"].astype(int),
        "Link clicks": m["clicks"].astype(int),
        "Results": m["platform_conv"].round(0).astype(int),
        "Result indicator": "actions:offsite_conversion.fb_pixel_custom.StartApplication",
    }).sort_values(["Day", "Campaign name", "Ad set name"])
    out.to_csv(INBOX / "meta_ads_adset_export.csv", index=False)


def write_linkedin_creatives(ad: pd.DataFrame) -> None:
    li = ad[ad["channel"] == "linkedin"]
    out = pd.DataFrame({
        "Start Date (in UTC)": [f"{d.month}/{d.day}/{d.year}" for d in li["date"]],
        "Campaign Name": li["campaign"],
        "Campaign ID": li["campaign"].map(CAMPAIGN_IDS),
        "Creative Name": li["ad_group"],
        "Total Spent": li["spend"].map(lambda x: f"{x:.2f}"),
        "Impressions": li["impressions"].astype(int),
        "Clicks": li["clicks"].astype(int),
        "Conversions": li["platform_conv"].round(0).astype(int),
    }).sort_values(["Campaign Name", "Creative Name"])
    preamble = ("Creative Performance Report\n"
                "Date Range: 1/5/2026 - 7/5/2026\n"
                "Account: University Graduate School - MBA (507123456)\n"
                "Currency: USD\n\n")
    (INBOX / "linkedin_creative_export.csv").write_text(
        preamble + out.to_csv(index=False), encoding="utf-8")


def write_ga4(df: pd.DataFrame, rng) -> None:
    smap = {"google_search": "google / cpc", "google_demandgen": "google / cpc",
            "meta": "facebook / paid_social", "linkedin": "linkedin / paid_social"}
    paid = df.copy()
    # GA4 sees last-click-ish key events (true conversions, ~92% tag capture) and
    # sessions roughly = clicks with tracking loss
    capture = rng.binomial(paid["true_conv"].astype(int), 0.92).astype(float)
    g = pd.DataFrame({
        "Date": paid["date"].dt.strftime("%Y%m%d"),
        "Session source / medium": paid["channel"].map(smap),
        "Session campaign": paid["campaign"],
        "Sessions": (paid["clicks"] * rng.uniform(0.82, 0.95, len(paid))).astype(int),
        "Engaged sessions": (paid["clicks"] * rng.uniform(0.45, 0.65, len(paid))).astype(int),
        "Key events": capture.astype(int),
    })
    # organic / direct baseline rows (the MMM's baseline traffic; not ad-attributable)
    dates = pd.date_range(START, periods=DAYS, freq="D")
    org_rows = []
    for src, base_sessions, base_keyev in [("google / organic", 210, 2.1),
                                           ("(direct) / (none)", 120, 1.4)]:
        t = np.arange(DAYS)
        season = 1.0 + 0.25 * np.sin(2 * np.pi * (t + 20) / 182.0)
        sess = (base_sessions * season * rng.lognormal(0, 0.15, DAYS)).astype(int)
        kev = rng.poisson(base_keyev * season)
        org_rows.append(pd.DataFrame({
            "Date": dates.strftime("%Y%m%d"), "Session source / medium": src,
            "Session campaign": "(not set)", "Sessions": sess,
            "Engaged sessions": (sess * 0.5).astype(int), "Key events": kev}))
    out = pd.concat([g] + org_rows, ignore_index=True).sort_values(
        ["Date", "Session source / medium", "Session campaign"])
    preamble = ("# All Users\n"
                "# Traffic acquisition: Session source/medium\n"
                f"# {pd.Timestamp(START):%Y%m%d}-{(pd.Timestamp(START) + pd.Timedelta(days=DAYS - 1)):%Y%m%d}\n")
    (INBOX / "ga4_traffic_acquisition.csv").write_text(
        preamble + out.to_csv(index=False), encoding="utf-8")


def main() -> None:
    INBOX.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    df, truth = _simulate(rng)

    write_google(df)
    write_meta(df)
    write_linkedin(df)
    write_ga4(df, rng)

    ad = _split_ad_groups(df, rng)
    write_google_adgroups(ad)
    write_meta_adsets(ad)
    write_linkedin_creatives(ad)

    # per-ad-group truth: spend/claims from the exact decomposition + the INTENDED
    # decode fields (None = deliberately non-conforming, must land in "(unparsed)")
    by_ad_group: dict = {}
    for camp, groups in AD_GROUPS.items():
        agg = ad[ad["campaign"] == camp].groupby("ad_group")
        by_ad_group[camp] = {
            gname: {
                "spend": float(agg.get_group(gname)["spend"].sum().round(2)),
                "platform_claimed_conversions":
                    float(agg.get_group(gname)["platform_conv"].sum().round(1)),
                "conforms_to_naming_convention": dec is not None,
                "intended_decode": dec,
            }
            for gname, _wt, dec in groups
        }

    by_channel: dict = {}
    for name, t in truth.items():
        c = by_channel.setdefault(t["channel"], {"total_spend": 0.0,
                                                 "true_start_applications": 0.0,
                                                 "incremental_start_applications": 0.0,
                                                 "platform_claimed_conversions": 0.0})
        for k in c:
            c[k] = round(c[k] + t[k], 1)
    json.dump({"scenario": "university MBA program, 26 weeks",
               "kpi": "GA4 key event start_application",
               "by_campaign": truth, "by_channel": by_channel,
               "by_ad_group": by_ad_group},
              open(INBOX / "_ground_truth.json", "w", encoding="utf-8"), indent=2)

    (INBOX / "README.md").write_text("""# Sample platform exports — MBA scenario

Generated by `scripts/generate_sample_exports.py` (seeded, regenerable). These are the
fixture files for the file-drop ingestion milestone: the mapping layer must turn THESE
(quirks and all) into the canonical schema.

**Ingest everything in this folder:** `python scripts/ingest.py --inbox`
(format auto-detected per file; unknown files are skipped loudly, never guessed).

| file | quirks the mapper must survive |
|---|---|
| google_ads_campaign_report.csv | 2 title rows before the header; quoted thousands separators in Impr./Clicks; fractional Conversions (data-driven attribution); `Total: Campaigns` row at the bottom |
| meta_ads_export.csv | clean CSV but verbose headers ("Amount spent (USD)", "Link clicks"); Results is a custom pixel event |
| linkedin_campaign_export.csv | 5 preamble lines incl. blank; M/D/YYYY dates; currency only stated in the preamble |
| ga4_traffic_acquisition.csv | `#` comment preamble; YYYYMMDD dates; source/medium needs mapping to channels; organic/direct rows must not become ad rows; Key events = start_application (the KPI) |
| google_ads_adgroup_report.csv | campaign-report quirks + `Ad group` column (ad-GROUP grain) |
| meta_ads_adset_export.csv | meta-export shape + `Ad set name` column (ad-SET grain) |
| linkedin_creative_export.csv | linkedin-export shape + `Creative Name` column (creative grain — LinkedIn has no ad-set tier) |

The ad-level files decompose the campaign-level numbers EXACTLY (same DGP), so
ingesting either grain — or both — reconciles to the same totals: the store keeps the
finer grain and drops the campaign-level aggregate when both are present. Ad set /
creative names follow the naming generator's grammar except ~15% deliberately
non-conforming names, which the decoder must file under `audience_type="(unparsed)"`,
never guess.

`_ground_truth.json` holds the DGP's true per-channel numbers (incl. true vs
platform-CLAIMED conversions — platforms over-claim by design here) plus per-ad-group
spend/claims and each name's INTENDED decode, so the mapping + decode work can be
validated the same way the MMM is. GA4 key events stay campaign-level: audience- and
creative-level conversions are platform-claimed ONLY.

Spend split (~$100k / 26 wk): google_search ~$30k, google_demandgen ~$15k,
meta ~$30k, linkedin ~$25k. KPI: `start_application` key events in GA4;
CRM matchback is a later phase.
""", encoding="utf-8")

    n_names = sum(len(g) for g in AD_GROUPS.values())
    n_bad = sum(1 for g in AD_GROUPS.values() for _n, _w, dec in g if dec is None)
    print(f"Wrote 7 export files + ground truth + README to {INBOX}")
    print(f"  ad-level: {n_names} ad-group/creative names, {n_bad} non-conforming "
          f"({n_bad / n_names:.0%}) -> '(unparsed)' bucket")
    for name, t in by_channel.items():
        print(f"  {name:<18} spend ${t['total_spend']:>9,.0f}  "
              f"true start-apps {t['true_start_applications']:>6,.0f}  "
              f"platform claims {t['platform_claimed_conversions']:>6,.0f}")


if __name__ == "__main__":
    sys.exit(main())
