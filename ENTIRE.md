# Entire × TenderOps (Neathacks 2026)

TenderOps uses **Entire** as its Continuous Memory: every supervised pipeline run
records an Entire-style checkpoint (request + agent decisions + changed files +
validation, linked to the git commit), and agents consult past checkpoints and
the code graph *before* building.

## 1. Install + enable (per hack guide)

```powershell
# Windows (stable)
irm https://entire.io/install.ps1 | iex
entire version
```

```bash
# macOS / Linux
brew install --cask entireio/tap/entire
# or: curl -fsSL https://entire.io/install.sh | bash
```

```bash
cd "TenderOps New"
entire enable -y --agent opencode   # hooks capture agent sessions
entire login                        # Sign in with GitHub (whole team)
# Org admin imports the repo in Entire (GitHub App) — then it appears for all.
```

Commit to create the first checkpoint (answer **yes** when asked to link it):

```bash
git add -A
git commit -m "Build first feature with Entire"
entire checkpoint list
entire checkpoint explain HEAD
```

## 2. Graph + Brain (Continuous Memory)

```bash
entire plugin install graph
entire graph init-agents --repo .     # writes .entire/graph-agent.md (+ AGENTS.md/CLAUDE.md blocks)
entire graph stats --repo .           # token savings vs grep

curl -fsSL https://raw.githubusercontent.com/entireio/entire-brain/main/scripts/get-brain.sh | bash
entire brain init-agents              # writes .entire/agent-guide.md
entire brain mcp --print-config       # register with MCP clients
```

This repo already ships the generated files (`.entire/graph-agent.md`,
`.entire/agent-guide.md`, `.entire/brain/facts.md`, `AGENTS.md`, `CLAUDE.md`),
so fresh agents get them on first checkout — no setup step to forget.

## 3. What TenderOps built with Entire

| Hack idea | TenderOps implementation |
|---|---|
| Before-you-build assistant | `POST /api/entire/brief` + `scripts/entire_brief.py` → past attempts, rejected approaches, reusable code via Graph |
| Reviewer that checks intent | `POST /api/entire/review` + `scripts/entire_review.py` → missed requirements, missing transcript/tests |
| Architecture drift detector | `scripts/entire_drift.py --save/--diff` → Graph snapshot new/removed deps |
| Checkpoint-to-demo generator | `GET /api/entire/checkpoints/{id}/demo` + `scripts/entire_demo.py` |
| Regression test scout | `GET /api/entire/scout` + `scripts/entire_scout.py` |
| Onboarding Q&A | `GET /api/entire/search?q=...` (checkpoints + code together) |
| Default runners | `entire runner setup -y`, then commit the generated `.entire/runners/` files |

Core: `app/services/entire_memory.py` (offline-first; shells out to
`entire` / `entire-graph` only when installed).
Auto-capture: `OrchestratorService.run_tender()` records a checkpoint per run.
UI: `web/app/entire/page.js`. Tests: `tests/test_entire_memory.py`.

## 4. Submission checklist

- Project: TenderOps — agentic tender execution with Entire Continuous Memory.
- Entire usage: Checkpoints (auto per run) + Agentic Search + Graph + Brain.
- Demo: run the API/UI flow (`/api/entire/*`, `/entire` page) and record it.
- Repository: this repo + setup in README.
- Checkpoint: Entire URL `https://entire.io/gh/OWNER/REPO/commit/FULL_COMMIT_SHA`,
  verified with `entire status`. See `docs/ENTIRE_SUBMISSION.md`.
