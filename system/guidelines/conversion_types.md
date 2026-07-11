# Conversion types — who measured it, and how much to trust it

The honesty backbone: agents must label every conversion number with its source
system; this file defines the vocabulary and the trust rules. Claim-ratio bands
below are **starting heuristics (Claude-drafted 2026-07-12, Harry to monitor)** —
the right band is campaign-specific, so treat these as tripwires for a human look,
never as verdicts, and tighten them per client as history accumulates.

## The three attribution systems (never sum across them)

1. **Platform-claimed** (`conversions`): the ad platform grading its own homework —
   view-through, modeled, overlapping credit. Expect over-claiming; the fixtures'
   observed range is ~1.05–1.8x per channel. Use for: within-platform direction,
   audience/creative comparison (the only grain that has it). Never for: cross-channel
   budget decisions presented as fact.
2. **Analytics-measured** (`key_events`, GA4): one consistent yardstick across
   channels, last-click-ish, campaign grain only. Use for: cross-channel cost
   comparisons, the claims-vs-measured gap. Still NOT incrementality.
3. **CRM-verified / modeled** (business KPI + MMM): the only place causal language is
   allowed, and even then hedged with intervals.

## Claim-ratio interpretation (claimed ÷ analytics-measured)

**The strongest signal is CHANGE against the account's own baseline, not the level.**
A stable 2.5x is that account's attribution personality; a jump from 1.5x to 2.5x in
a month with an unchanged channel mix means a tracking or settings change — hunt for
it (new pixel events, attribution-window change, consent-mode update, tag loss).

Starting tripwires by level (monitor and adjust):

| ratio | read | agent behavior |
|---|---|---|
| **< 0.9x** | platforms claiming LESS than analytics measures — usually broken platform tracking (pixel misfire, consent losses) or GA4 double-counting a key event | `investigate_tracking`, worded as a measurement issue, not performance |
| **0.9 – 2.0x** | normal attribution inflation; search-heavy mixes sit at the low end | label it, no flag |
| **2.0 – 3.5x** | elevated but common for Meta/TikTok- and retargeting-heavy mixes (view-through + modeled credit) | no flag if stable; flag if newly arrived |
| **> 3.5x** | claims are mostly attribution artifacts — check event dedup (double-fired events), 28-day view-through windows, retargeting share, GA4 tag coverage | `investigate_tracking`; until resolved, commentary leans on measured numbers only |

Platform priors (direction, not law): search lowest (click-based attribution),
LinkedIn middle, Meta/TikTok highest (view-through + modeled). A retargeting-heavy
account runs higher everywhere — audiences already visiting convert "through" ads
they barely needed.

## Matchback rules (ASSUMPTIONS — confirm per client in the brief)

- Join preference, best first: click ID → hashed email → last-touch UTM + date window.
- Default attribution window: 30 days from key event to CRM record; longer decision
  cycles (postgraduate programs) may justify 90 — say which window every time.
- Count the applications STARTED series for pacing, applications COMPLETED for
  outcomes; never blend the two in one series.
- CRM lag is real: the latest 1–2 weeks are always understated — commentary must not
  read the last fortnight's "drop" as performance (cohort it or exclude it).
- **Always report the match RATE alongside matchback results** ("62% of CRM records
  matched to a touch") — without it, matchback silently becomes attribution among
  the matchable, and the unmatched share is where the story often hides.
