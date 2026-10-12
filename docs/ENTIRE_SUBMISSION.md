# Entire submission — TenderOps (Neathacks 2026)

## Project

TenderOps: agentic tender execution (P1 Tender Analyst → P2 Evidence →
P3 Compliance → P4 BidPrep + independent Verification, supervised by
`app/orchestrator/`). Entire adds Continuous Memory: every run becomes a
checkpoint future agents learn from.

## Entire usage

- **Checkpoints**: auto-recorded by `OrchestratorService.run_tender()`
  (`app/orchestrator/service.py`) into `./data/entire_memory/`; manual via
  `POST /api/entire/checkpoints`. Inspect: `entire checkpoint list`,
  `entire checkpoint explain HEAD`.
- **Agentic Search**: `GET /api/entire/search?q=...` searches past checkpoints
  + code together (CLI passthrough when `entire` is installed).
- **Entire Graph**: `GET /api/entire/graph/search|stats|impact`,
  `scripts/entire_drift.py`; agent instructions in `.entire/graph-agent.md`.
- **Entire Brain**: `.entire/agent-guide.md` + `.entire/brain/facts.md`;
  `GET /api/entire/brain/facts`; session carryover across agents.

## Demo

1. `uv run uvicorn app.main:app --reload` (+ `cd web && npm run dev`).
2. Run a tender, open `/entire`, click Checkpoints → Brief → Review → Demo.
3. Or CLI: `scripts/entire_brief.py`, `entire_review.py --latest`,
   `entire_demo.py --latest`, `entire_scout.py --latest`.

## Repository

Setup and run instructions: README + ENTIRE.md.

## Checkpoint

- Entire checkpoint link: `https://entire.io/gh/OWNER/REPO/commit/FULL_COMMIT_SHA`
  (replace OWNER/REPO/SHA after `entire login` + org-admin import + push;
  verify with `entire status`).
- Local checkpoint id (this submission's work): fill in after commit, e.g. `ckpt_<hex>`.
