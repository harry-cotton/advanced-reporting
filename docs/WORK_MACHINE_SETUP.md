# Work-machine bring-up (FBI Talent Acquisition engagement)

Step-by-step to make a fresh clone fully operational. Written 2026-07-14, after the
P0–P5 build merged to `main` (`7f14712`). Everything regenerates deterministically
from the repo except the API key.

**Known constraints on Harry's work machine** (adapt as found):
- `winget` and `uv` installs are **blocked by group policy** — use python.org
  installers and plain `pip`.
- System Python is **3.14** — fine for the app itself, but TensorFlow/Meridian
  almost certainly has no 3.14 wheels. Prefer installing **Python 3.12** from
  python.org (per-user install needs no admin). If 3.12 is impossible, use 3.14
  and set `modeling.engine: baseline` (skip step 4) — everything else works.
- The repo may live under `C:\Users\<user>\...`. If the path contains `OneDrive`,
  MOVE the clone to a plain local folder (e.g. `C:\dev\advanced-reporting`) before
  doing anything — a sync layer can lock/corrupt `.git` (see CLAUDE.md).

Run everything from the repo root in PowerShell. `pytest -q` green is the gate.

## 1. Code

```powershell
git checkout main
git pull
git log --oneline -1     # expect: 7f14712 Merge fbi-recruitment-mmm: ...  (or newer)
```

## 2. Python environment

Preferred: Python 3.12 from https://www.python.org/downloads/ (choose the per-user
install; no admin needed). Then:

```powershell
py -3.12 -m venv .venv           # or: python -m venv .venv  (if 3.12 is default)
.\.venv\Scripts\pip install -e .
.\.venv\Scripts\python.exe --version    # expect 3.12.x
```

If venv creation fails with "unable to copy ...": the source Python is likely the
sandboxed Microsoft Store build — install from python.org and retry with `py -3.12`.

## 3. The two gitignored files

**a) `config\config.yaml`** — copy the committed snapshot:

```powershell
copy config\config.fbi-example.yaml config\config.yaml
```

If skipping Meridian (step 4), edit it: `modeling.engine: baseline`.

**b) `system\context\client_brief.md`** — per-engagement agent context (gitignored
by policy; this engagement is fictional, so the content is reproduced here).
Create the file with exactly this content:

```markdown
# Client brief — Federal Bureau of Investigation — Talent Acquisition (FICTIONAL demo engagement)

> Clearly-fictional synthetic engagement; cover names are deliberate, no real client data.

- **Who / what they sell:** federal law-enforcement recruiting across five career
  paths: Special Agent (hero), Intelligence Analyst, Cyber/STEM, Linguists,
  Professional Staff.
- **Campaign goal in their words:** drive qualified submitted applications on the
  Bureau's own careers portal, sustained across FY24–26.
- **KPI wiring:** `key_events` = application starts measured by GA4 on the careers
  portal; `conversions` = platform-claimed. Weekly CRM matchback of submitted
  applications IS wired in — it is the MMM target (a count). Post-submission
  pipeline stages are an ATS export: selection outcomes, right-censored, REPORTING
  ONLY. Media buys applications; it cannot pass a polygraph.
- **Budget + flight:** $37,500,000 paid media across 131 weeks (2024-01-01 →
  2026-07-05, ~$15M/yr).
- **Channels in play:** google_search, youtube, meta, linkedin, display, ctv,
  audio, jobboards. TikTok off-limits (federal device ban). Non-paid: organic
  search, direct, email, organic social.
- **Targets the client holds us to:** cost per application start good ≤ $160,
  watch ≤ $220; cost per INCREMENTAL submitted application (modeled) good ≤ $400,
  cut above $650. Different denominators — never blend the two bands.
- **Sensitivities:** government audience — hedge causal claims. CTV claims
  conversions GA4 can't verify (no UTM path) — expected, not scandalous. LinkedIn
  dark 6 weeks Oct–Nov 2025 (budget freeze). National Recruiting Week bursts (May
  2025/2026) explain the spend-spike flags.
- **What would make this report a win:** a defensible cost-per-incremental-
  application story by channel, claims honestly separated from GA4, and the
  pipeline reported without pretending media controls it.

## Data egress

Synthetic demo: no PII, budgets/targets OK to send. agent.enabled: true stands.
```

## 4. Meridian (optional; needs Python 3.12)

```powershell
.\.venv\Scripts\pip install google-meridian
```

Heavy (TensorFlow). If skipped, set `modeling.engine: baseline` in config —
the Incrementality page then runs on the baseline engine (wider intervals).
CI never fits Meridian either way.

## 5. Data + pipeline (deterministic; ~500MB local, all gitignored)

```powershell
.\.venv\Scripts\python.exe scripts\generate_fbi_campaign.py
.\.venv\Scripts\python.exe scripts\ingest.py --inbox "data/MMM Data" --reset
.\.venv\Scripts\python.exe scripts\run_pipeline.py
```

Expected checks:
- generator prints `BLENDED cost/start $169 (target 150-200)` and
  `SA pipeline cumulative 7.3%`;
- ingest prints `Consolidated 5 pull(s) -> 870,226 canonical daily rows`;
- pipeline (5–10 min with Meridian — MCMC) ends `engine=meridian R2=0.992
  holdoutR2=0.997` and `ground-truth recovery: PASS — 8/8 channels within 2x`.
  (Baseline engine: seconds, lower recovery numbers — that's expected.)

## 6. AI spec/commentary + client report (optional; needs the API key)

The key is NOT in the repo. Either `setx ANTHROPIC_API_KEY "sk-ant-..."` (new
terminal afterwards) or a repo-root `.env` line `ANTHROPIC_API_KEY=...`.

```powershell
.\.venv\Scripts\python.exe scripts\advise.py --spec --commentary
.\.venv\Scripts\python.exe scripts\build_report.py
```

Writes `outputs/report_spec.json`, `outputs/commentary_ai.md` + `.json` (the
block-tagged sidecar the dashboard weaves under charts), `outputs/client_report.html`.
Without a key everything else still works — the dashboard just shows a
"no AI commentary yet" note.

## 7. Run it

```powershell
.\.venv\Scripts\python.exe -m streamlit run src\advanced_reporting\dashboard\app.py --server.port 8599
.\.venv\Scripts\python.exe -m pytest -q     # expect 317 passed, 1 skipped
```

Dashboard at http://localhost:8599 — seven pages: Exec Summary, Channels,
Audiences, Geography, Incrementality, Data Quality, Explore.
