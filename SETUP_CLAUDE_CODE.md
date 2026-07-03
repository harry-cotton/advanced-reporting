# Setting up Claude Code in VS Code

A practical, Windows-flavored walkthrough to get Claude Code running in VS Code and pointed
at this project. (Steps verified against the official docs — see Sources at the bottom.)

## 0. Before you start

- **VS Code 1.98.0 or newer** (check Help → About; update if older).
- **An Anthropic account** — any paid Claude subscription (Pro, Max, Team, or Enterprise) or a
  Claude Console account. **No API key required**; you sign in in the browser.

## 1. Put the project in a normal (non-OneDrive) folder

This matters: a live `.git` inside a OneDrive-synced folder corrupts. Clone the repo into a
plain local dev folder.

```powershell
# pick any non-synced location
mkdir C:\dev
cd C:\dev
# clone from GitHub — the repo of record (never seed from a bundle or OneDrive copy)
git clone https://github.com/harry-cotton/advanced-reporting.git
cd advanced-reporting
```

After this, also copy `CLAUDE.md` into the repo if it isn't already there, and commit it —
Claude Code reads it automatically (see step 6).

> Don't have `git` yet? Install Git for Windows from git-scm.com, then reopen PowerShell.

## 2. Install the Claude Code extension

In VS Code: press **Ctrl+Shift+X** to open Extensions, search **"Claude Code"**, click
**Install** (publisher: Anthropic). Or use the Marketplace link in Sources below.

If it doesn't show up after installing, run **Developer: Reload Window** from the Command
Palette (**Ctrl+Shift+P**).

## 3. Open the project

**File → Open Folder…** → select `C:\dev\advanced-reporting`. Accept "trust the authors" so
the extension can run (it won't work in Restricted Mode).

## 4. Open the Claude Code panel

Any of these:

- The **Spark icon** in the editor toolbar (top-right) — *only appears when a file is open*.
- The **Spark icon** in the left Activity Bar (always visible) → sessions list.
- **Status bar**: click **✱ Claude Code** (bottom-right) — works with no file open.
- **Command Palette** (**Ctrl+Shift+P**) → type "Claude Code" → "Open in New Tab".

You can drag the panel to the right sidebar to keep it visible while you code.

## 5. Sign in

The first time the panel opens, click **Sign in** and finish authorization in your browser.
If you later see "Not logged in · Please run /login", the sign-in screen reopens automatically.

## 6. Confirm it picked up CLAUDE.md

This project ships a `CLAUDE.md` at the repo root — Claude Code **auto-loads it** as project
context every session, so you don't have to paste it in. To sanity-check, ask:

> What does CLAUDE.md say this project's target MMM engine is?

It should answer "Google Meridian" without you attaching anything. (You can also `@CLAUDE.md`
to reference it explicitly.)

## 7. Use Plan mode (recommended for this workflow)

Since you'll brainstorm in Cowork and implement here, Plan mode is the sweet spot: Claude
writes out *what* it will do and waits for your approval before changing files.

- Click the **permission-mode indicator** at the bottom of the prompt box → choose **Plan**.
- VS Code opens the plan as a markdown doc you can add inline comments to before approving.
- To make Plan the default: settings (**Ctrl+,**) → Extensions → Claude Code →
  set **`initialPermissionMode`** to `plan`.

When Claude proposes edits, you get a **side-by-side diff** to accept, reject, or tweak.

## 8. Day-to-day: the brainstorm → implement loop

1. Brainstorm / decide direction in **Cowork**.
2. Paste the resulting brief into Claude Code here.
3. Let it draft a **plan** (Plan mode), review/comment, approve.
4. It implements, runs `pytest`, and you review diffs.
5. Ask it to commit and open a PR:
   > commit my changes with a descriptive message
   > create a pr for this feature

Handy input tricks: **@-mention** files/folders (`@src/advanced_reporting/mmm/`), select code
and press **Alt+K** to insert a line-range reference, and **Shift+Enter** for a newline.

## 9. Optional: the standalone CLI

The extension bundles its own engine for the chat panel. A few features (e.g. `claude mcp add`,
git worktrees) need the **standalone CLI** so you can run `claude` in the integrated terminal.
Install it from the Claude Code setup docs (Sources) only if/when you need those.

## 10. Optional: connectors & plugins

- **Plugins**: type `/plugins` in the prompt box to browse/install (e.g. a marketing pack).
- **MCP servers** (live data like Supermetrics later): add via the integrated terminal with
  `claude mcp add …`, then manage with `/mcp` in the panel.

## Quick troubleshooting

- **No Spark icon?** Open a file first (the toolbar icon needs one), confirm VS Code ≥ 1.98,
  Reload Window, and disable other AI extensions (Cline/Continue) if they conflict.
- **Stuck on sign-in with an API key set?** Launch VS Code from a terminal with `code .` so it
  inherits your environment, or just sign in with your Claude account.

---

Sources: [Use Claude Code in VS Code](https://code.claude.com/docs/en/vs-code) ·
[Claude Code for VS Code — Marketplace](https://marketplace.visualstudio.com/items?itemName=anthropic.claude-code) ·
[Claude Code setup / CLI](https://code.claude.com/docs/en/setup)
