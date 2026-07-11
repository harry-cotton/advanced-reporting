# Campaign types — how to recognize them and what "primary" means for each

<!-- HARRY: this is YOUR domain knowledge. The spec agent uses it to pick the
     campaign_type, kpi_label, primary tier and insight blocks. Fill each section;
     delete examples you disagree with. Keep it opinionated — vague guidance
     produces vague dashboards. -->

## How to classify a campaign from its data

Signals the agent may use (all computed, all in the summaries it receives):
channel mix, presence/absence of GA4 key events, conversion volumes vs spend,
campaign-name conventions, audience types (PROSPECT/RETARGET share), flight length.

<!-- e.g. "If GA4 key events exist and >60% of spend is search + LinkedIn, treat as
     lead generation." Write the rules you actually apply when you open an account. -->

## Types

### Lead generation (e.g. university program, B2B pipeline)
- Primary KPI: analytics-measured key events (form starts/submissions); CRM matchback
  when available. Platform-claimed conversions are directional ONLY.
- Primary tier: outcome. Watch: cost/key event trend, claim ratio, search brand vs
  non-brand split.
- TODO(Harry): healthy CPL ranges by sector, seasonality notes, typical flight length.

### Recruitment (e.g. federal/agency hiring)
- TODO(Harry): what differs from lead gen (application funnels, veteran/role
  audiences, compliance constraints on targeting language).

### Awareness / brand
- Primary tier: reach. A conversion KPI on an awareness campaign is a misreading —
  say so rather than reporting it as failure.
- TODO(Harry): when video views vs impressions lead; VTR expectations by format.

### E-commerce / ROAS
- Primary KPI: platform revenue → blended ROAS (until MMM exists; then modeled ROI).
- TODO(Harry): claim-ratio expectations when a pixel + server-side tagging exist.

## Never do
- Never grade an awareness campaign on CPA, or a lead-gen campaign on ROAS it
  doesn't measure.
- Never present platform-claimed conversions as the primary KPI when an
  analytics-measured series exists.
