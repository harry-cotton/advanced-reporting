# Campaign types — how to recognize them and what "primary" means for each

Authored by Harry (2026-07-12), structured for the spec agent. Sectors served: **higher
education**, **local/state government outreach**, **recruitment marketing**. Every
campaign is one of three funnel types — awareness, engagement, conversion — and the
type decides the primary KPI, the primary tier, and which insight blocks lead.

## The one principle that overrides everything

**An efficiency metric is always the primary number** — cost per <the outcome this
campaign exists to buy>. Volume without cost is a vanity slide; cost without volume is
an excuse. This is why the house combo chart (volume bars + efficiency line) is the
default visual: both halves of the story, one chart. Choose the efficiency metric to
match the funnel type, never a tier above or below it.

## How to classify a campaign (rules in precedence order)

All signals exist in the computed summaries; when they conflict, the HIGHER rule wins,
and the conflict goes in watch_flags rather than being silently resolved.

1. **Objective token in decoded names** — campaign/ad-set names carrying the naming
   grammar's objective (AWARENESS → awareness, ENGAGE/CONSIDER → engagement,
   CONVERT → conversion) are declarative: believe them.
2. **Conversion-event wiring** — a populated conversion event / GA4 key-events series
   means conversion (or engagement being conversion-measured); awareness and
   engagement campaigns usually have NO conversion event configured. Absence of a
   conversion event on a claimed "conversion" campaign is itself a watch_flag.
3. **Audience type** — RETARGET/ENGAGED audiences mean engagement or conversion,
   never awareness (you don't build reach against people you already reached).
   Majority-PROSPECT with no conversion event leans awareness.
4. **Placement/format mix** — video/reach placements with broad audiences lean
   awareness; feed + retargeting leans conversion.

## The three types

### Awareness
- **Primary tier:** reach. **Primary efficiency:** CPM (video-heavy: CPV/VTR).
- **Blocks that lead:** reach-tier scorecard, spend mix, impressions/CPM combo.
- **The client's boss asks:** "how many of the right people did we reach, at what cost?"
- **THE TRAP:** someone will call out an expensive cost per conversion. **It does not
  matter — conversions are not the goal of this campaign.** Do not show CPA as a
  primary awareness metric; if conversions happen, report them as a bonus footnote,
  never as a grade.

### Engagement
- **Primary tier:** intent. **Primary efficiency:** cost per engagement (engaged
  session / video view / page-depth, per what's measured).
- **Blocks that lead:** intent-tier scorecard, sessions/engagement-rate views,
  cost-per-engaged-session combo.
- **THE TRAP:** **a high CPM is not automatically bad here.** Premium placements and
  narrower audiences cost more per impression but can win on cost per engagement —
  judge on the engagement efficiency, and flag CPM only when cost per engagement is
  ALSO poor.

### Conversion
- **Primary tier:** outcome. **Primary efficiency:** cost per analytics-measured key
  event when the series exists; cost per platform-claimed conversion (labeled) when
  it doesn't.
- **Blocks that lead:** outcome scorecard, claims-vs-measured, cost-per-outcome
  ranking, audience efficiency (within-type).
- **THE TRAP:** **an expensive efficiency metric is not automatically a failure.**
  High-intent tactics (brand search, retargeting, niche professional audiences) can
  carry higher unit costs and still be the most effective spend — judge cost against
  the tactic's role and its volume, compare within audience type, and only flag when
  a tactic is expensive AND underdelivering its job.

## Sector notes

- **Higher education:** lead-gen shaped (inquiries → applications). Strong intake
  seasonality — compare year-over-year or intake-over-intake, not raw
  month-over-month, near application deadlines. Search converts exceptionally well
  in this vertical (see playbook bands); LinkedIn earns its high CPCs for
  postgraduate/professional programs, rarely for undergraduate.
- **Local/state government outreach:** frequently awareness/engagement by design —
  programs, services, compliance messaging. Often NO legitimate conversion event
  exists; that is not a data gap, it is the campaign type (watch_flag only if the
  brief claims conversions). Keyword competition is low, so search costs run well
  below commercial benchmarks; judge reach efficiency, not lead volume.
- **Recruitment marketing:** the conversion is an application (apply-start /
  apply-complete). Job-board/programmatic buying is CPC/CPA-based and benchmarked
  separately (see playbook); healthcare and hard-to-fill roles legitimately run
  multiples of the standard cost-per-application bands.

## Never do
- Never grade an awareness campaign on CPA, or judge engagement on CPM alone.
- Never present platform-claimed conversions as the primary KPI when an
  analytics-measured series exists.
- Never apply one CTR/CPC target across search and social — different auctions,
  different bands (see playbook).
