# Entire Brain — TenderOps agent guide

Continuous memory for this repo. Sessions live in the repo, not in one agent:
start work in one agent, pick it up in another, context carries over.

## How agents use the brain here

1. **Before editing** ("before you build"): call the briefing helper first.
   - API: `POST /api/entire/brief` with `{"query": "<what you plan to build>"}`.
   - CLI: `uv run python scripts/entire_brief.py --query "<plan>"`.
   - It searches previous Entire checkpoints (past tender runs, rejected approaches,
     edge cases) and locates reusable code via Entire Graph. Include the top 3
     findings in your plan.
2. **After a run**: record an Entire checkpoint so the next agent learns from it.
   - Automatic: `OrchestratorService.run_tender()` records one best-effort on every
     supervised run (see `app/services/entire_memory.py:record_checkpoint`).
   - Manual: `POST /api/entire/checkpoints` or
     `uv run python scripts/entire_demo.py --tender-id <id>`.
3. **When reviewing**: use `POST /api/entire/review` (intent vs. change checker)
   before approving a checkpoint.
4. **Session carryover**: facts worth keeping across sessions go in
   `.entire/brain/facts.md` (short bullets with tender_id / checkpoint id links).
   Read that file at session start; append to it when you learn something durable
   (e.g. "EMD exemption needs NSIC cert — see ckpt_abc123").

## Fallback (no Entire CLI)

All helpers work offline: `app/services/entire_memory.py` uses the local store in
`./data/entire_memory/` and shells out to `entire` / `entire-graph` / `entire-brain`
only when the binaries exist. Never fail a run because Entire is missing.
