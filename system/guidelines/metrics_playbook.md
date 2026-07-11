# Metrics playbook — definitions, healthy ranges, classic misreadings

Bands below are **public industry benchmarks (published 2025–early 2026)**, gathered
2026-07-12 — directional starting points, NOT client targets. They set good/warn
gauge bands only until real client history exists; the client brief always overrides.
Wide variance is normal: geography, seasonality, audience narrowness and creative all
move these by 2x. Sources at the bottom.

## How bands map to the dashboard

`reporting.targets` takes `{good, warn}` per metric. Convention: **good** = at or
better than the benchmark midpoint; **warn** = worse than the weak end of the band;
between = amber. For costs lower is better; for rates higher is better. Where no band
is listed, leave the metric to the channel-spread fallback (honestly labeled).

## Search (Google Ads) — education & instruction vertical

| metric | band (education vertical) | notes |
|---|---|---|
| CPC | ~$4.70 – $8.40 (mid ≈ $6.20) | rose ~42% YoY into 2025 — expensive clicks are the market, not a failure |
| CTR | ~4% – 6% | search CTR; NEVER applied to social |
| CVR | ~8% – 13% | education converts unusually well on search |
| CPL / cost per key event | ~$68 – $122 (mid ≈ $90) | inquiry/lead grain, not enrollment |

**Gov/public-sector search:** CPC ≈ **$1.50 – $2.50** (low competition). Judge
against these, not commercial bands.

## Meta (Facebook/Instagram)

| metric | band | notes |
|---|---|---|
| CPM | ~$8 – $14 (education often < $8) | awareness-optimized buying lands at the low end |
| CPC | ~$0.70 (traffic) – $1.90 (lead gen) | objective changes the auction; don't mix |
| CTR | ~0.9% – 2.2% | feed CTR; a "bad" 1% here would be catastrophic on search and great on LinkedIn |
| CPL (education) | ~$15 – $29 (median ≈ $21) | strong seasonality (Feb peak, Jun trough) |

## LinkedIn

| metric | band | notes |
|---|---|---|
| CPC | ~$4 – $8 | professional targeting premium — expected, not a flag |
| CPM | ~$31 – $38 (competitive niches $50+) | high CPM is the price of precision here |
| CTR | ~0.4% – 0.7% | the lowest CTR band of any channel; normal |
| CPL | ~$64 (education) – $100+ (B2B mid-funnel $120–$250) | earns it for postgrad/professional programs |

## Recruitment (job boards / programmatic, Appcast 2025)

| metric | band | notes |
|---|---|---|
| Cost per application | ~$15 – $35 (most occupations) | healthcare + hard-to-fill roles legitimately exceed this |
| Apply rate (click → application) | ~5% – 6.5% | tech/marketing at the top end |
| Cost per hire | ≈ $851 avg | context metric, not a media KPI |

## Awareness-tier CPMs (cross-channel context)

Display (GDN) ≈ $3; YouTube ≈ $5 (CPV buying); Meta ≈ $8–14; LinkedIn ≈ $31–38;
CTV ≈ $25. **The spread is the point:** never compare CPMs across channels as if
cheap reach and precise reach were the same product.

## Classic misreadings (the agent must not write these)

- "CTR is below the 2% benchmark" — on LinkedIn or display, where 0.5% is normal.
  CTR bands are per-channel, full stop.
- "CPM is high, cut the channel" — on engagement/conversion campaigns judge cost per
  engagement/outcome; on LinkedIn high CPM is structural.
- "ROAS is 0, nothing is working" — most of these sectors don't measure revenue;
  ROAS simply doesn't apply (see conversion_types.md).
- "Costs rose vs last month" — in higher ed, near-deadline seasonality dominates;
  compare intake-over-intake.

## Sources (retrieved 2026-07-12)

WordStream/LocaliQ Google Ads benchmarks 2025 & 2026; MDMPPC education vertical;
PPCChief education CPC; WordStream Facebook Ads benchmarks 2025; SuperAds education
CPL tracker; ClosleyHQ / TheB2BHouse / HockeyStack LinkedIn benchmark reports
2025–26; Appcast 2025 Recruitment Marketing Benchmark Report (via HR Dive);
CPMCalculator / DigitalApplied display & video CPM compilations. Refresh yearly;
replace with client history as soon as 8+ weeks of it exists.
