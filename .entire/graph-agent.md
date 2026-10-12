# entire-graph agent instructions (TenderOps)

This repo uses Entire Graph for code navigation. Prefer it over grep.

## Rules

1. First move for "where is X implemented?" is `entire-graph search`, not grep.
2. Cite file paths with line numbers for every claim (e.g. `app/orchestrator/graph.py:36`).
3. Trace relationships: who calls this, what breaks if it changes.
4. For TenderOps pipeline questions, map to stages: P1 tender_analyst, P2 evidence_agent,
   P3 compliance_agent, P4 bidprep_agent + verification_agent, supervised by
   `app/orchestrator/` (`app/orchestrator/graph.py:36`).
5. If the `entire-graph` binary is missing, fall back to `app/services/entire_memory.py`
   `EntireGraph.locate()` (AST + text fallback) and say so in the answer.

## Useful commands

```bash
entire-graph search "request routing" --repo .
entire graph stats --repo .
```
