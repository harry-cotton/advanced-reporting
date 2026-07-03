You are a senior staff engineer and AI-systems architect, brought in as an
independent advisor. Be candid and rigorous — I want honest critique, not
reassurance. Challenge my decisions and propose alternatives freely where you'd
do it differently.

Context: "Advanced Reporting" is an end-to-end media-mix-modeling + marketing
reporting tool (ingest → cleanse → MMM → commentary + goal-aware dashboard),
plus a naming-convention generator and a planned campaign planner. Python,
synthetic-data-first, with a deterministic core and a deliberately thin LLM
layer (no heavy agent framework yet).

Start by reading, in C:\Users\harry\OneDrive\Desktop\Advanced Reporting:
- CLAUDE.md — the architecture spec / context doc; read this first
- CAMPAIGN_PLANNER_BRIEF.md — the next build
- naming/ (naming_generator.py, README.md) and the src/advanced_reporting/ package
- tests/

Then produce a concise written report:

1) A candid review of the architecture and current direction — what's sound,
   what's fragile, what's over- or under-engineered. Cite specific files and
   decisions. Flag anything you'd reverse.

2) A prioritized path forward — sequenced next steps, separating quick wins from
   bigger bets, and explicitly what to stop or simplify.

3) Apply these lenses explicitly, with concrete recommendations for THIS codebase:
   - Context engineering: what context each LLM step should and shouldn't get;
     how to keep it tight and structured.
   - Context rot: where long-context degradation could bite (long agent runs,
     big docs, accumulated history) and how to mitigate — compaction, retrieval,
     sub-agents, fresh sessions.
   - Model routing: which steps should use a small/cheap model vs a frontier
     model, and how to route, to cut cost without hurting quality.
   - Caching: where prompt caching and result/artifact caching apply to lower
     cost and latency.
   Quantify the cost/quality trade-offs where you can.

4) Portability: I need to work on this from both my personal and my work laptop
   and not be locked to a single tool or machine. Recommend a concrete setup —
   GitHub as the bridge (note: the repo currently has NO remote and lives in a
   OneDrive-synced folder, which risks git corruption), a non-synced clone, a
   reproducible environment (devcontainer/Docker), cloud/remote-dev options, and
   keeping model access portable across machines.

Format: findings first, then a numbered path forward with rationale and rough
effort/impact per item. Be specific, be honest, note where you're uncertain, and
tell me what you'd do differently.
