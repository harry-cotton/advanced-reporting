# Client brief — <client name>   (copy to client_brief.md; gitignored)

- **Who / what they sell:**
- **Campaign goal in their words:**
- **KPI wiring:** what counts as a conversion, where it's measured (GA4 event name,
  CRM object), matchback status
- **Budget + flight:** total, weeks, pacing expectations
- **Channels in play (and any that are off-limits):**
- **Targets the client already holds us to:** (these OVERRIDE playbook-derived bands)
- **Sensitivities:** compliance constraints, words to avoid, political context
- **What would make this report a win for the stakeholder reading it:**

## Data egress (decide per client — this file is sent to the model API)

This brief + `macro_notes.md` + compact computed summaries (never raw rows) go to
the Anthropic API at pipeline time when the agent layer runs. Default policy: no
PII in this file (no individual names/emails — role titles are fine), budgets and
targets OK. If the client's confidentiality terms forbid third-party processing,
set `agent.enabled: false` for this engagement and note it here.
