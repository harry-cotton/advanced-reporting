# Conversion types — who measured it, and how much to trust it

<!-- HARRY: the honesty backbone. The agents must label every conversion number with
     its source system; this file defines the vocabulary and the trust rules. -->

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

## Matchback rules
- TODO(Harry): how CRM matchback joins (email/click ID/date windows), lag assumptions,
  when a key event "counts".

## Claim-ratio interpretation
- TODO(Harry): what ratio ranges are normal per platform, and at what point a ratio
  signals a tracking problem rather than attribution inflation.
