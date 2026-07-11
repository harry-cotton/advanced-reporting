# The system knowledge base

This folder powers the agent system (see `AGENT_SYSTEM_BRIEF.md` at the repo root).
The layout is the mental model:

| folder | committed? | what it is |
|---|---|---|
| `guidelines/` | ✅ yes | Harry-authored domain guidance, reusable across every client — the IP. Changes here steer every client's output: review like code. |
| `context/` | ❌ gitignored | Per-client / per-engagement briefs and curated notes. Confidential, like `data/`. |
| `prompts/` | ✅ yes | The agent prompt templates, versioned like code. |

Agents read `guidelines/` + `context/` + compact computed summaries of the data —
never raw rows. Generated artifacts land in `outputs/` (gitignored), never here.

**The three laws** (from the brief): agents configure, the deterministic engine
computes, agents narrate over computed facts. No LLM produces a number; no number
appears in prose the engine didn't compute; nothing runs per page load.
