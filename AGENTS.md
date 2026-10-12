# AGENTS.md — TenderOps

## Project

Agentic tender execution: P1 Tender Analyst → P2 Evidence → P3 Compliance →
P4 BidPrep + independent Verification, supervised by `app/orchestrator/`.
API: `uv run uvicorn app.main:app --reload`. Tests: `uv run python -m pytest tests/`.

<!-- entire:graph:start -->
## Entire Graph

Agent code navigation is via Entire Graph. First move for "where is X?" is
`entire-graph search`, not grep. Instructions: `.entire/graph-agent.md`.
Fallback: `EntireGraph.locate()` in `app/services/entire_memory.py`.
Check savings: `entire graph stats --repo .`.
<!-- entire:graph:end -->

<!-- entire:brain:start -->
## Entire Brain (continuous memory)

Read `.entire/agent-guide.md` and `.entire/brain/facts.md` at session start.
Before editing, get a briefing: `POST /api/entire/brief` or
`uv run python scripts/entire_brief.py --query "<plan>"`.
Record checkpoints so the next session learns: they are auto-recorded by
`OrchestratorService.run_tender()` into `./data/entire_memory/`.
<!-- entire:brain:end -->
