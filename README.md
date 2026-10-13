# TenderOps

An Agentic AI system for autonomous tender execution, built with LangGraph. Analyzes tender documents, maps company evidence to compliance requirements, identifies eligibility gaps, prepares bid documents, and independently verifies bid readiness.

## Structure

```text
.
├── pyproject.toml       # project metadata + dependencies
├── uv.lock              # locked dependencies
├── .python-version      # pinned Python version
├── .env.example         # example env vars (copy to .env)
├── app/                 # main package (P1→P2→P3→P4 + orchestrator)
└── web/                 # Next.js UI
```

## Setup

1. Install [uv](https://docs.astral.sh/uv/) if you haven't already.
2. Install dependencies:

   ```bash
   uv sync
   ```

3. Copy `.env.example` to `.env` and fill in your credentials:

   ```bash
   cp .env.example .env
   ```

   Keys: `LITELLM_API_KEY` (primary LLM gateway), `GROQ_API_KEY` (fallback),
   `NEATLOGS_API_KEY` (run tracing).

## Running the application

Run the development server:

```bash
uv run uvicorn app.main:app --reload
```

## Running Tests

To execute the test suite (uses mocked LLM):

```bash
uv run python -m pytest tests/
```

## API Usage

Auth: sign up at `POST /api/auth/signup`, then send
`Authorization: Bearer <token>`. Evidence is isolated per company
(`POST /api/companies`, then `?company_id=<slug>`).

- `POST /api/tender-analysis` — P1: analyze a tender PDF.
- `POST /api/pipeline/run` — full pipeline: tender PDF → analysis (P1) →
  evidence map (P2) → compliance + blockers (P3) → bid draft + verification
  (P4). Pauses with a checkpoint when blockers need human fixes.
- `POST /api/pipeline/resume` — resume a paused run with fix documents.
- `POST /api/pipeline/evidence-index` — rebuild a company's evidence index
  from uploaded PDFs (form fields: `company_id`, repeatable `files`).

Upload PDFs using `multipart/form-data`.

## Evidence agent (P2)

Retrieves company evidence per requirement and judges support level:

- Company indexes live in Chroma (local MiniLM embeddings — no API spend).
- `EvidenceMap` statuses: `satisfied` | `partial` | `missing` | `needs_review`,
  each with confidence, source hits (doc + page + excerpt), and notes.
- Numbers rule: the mapper never converts units or compares figures —
  it quotes them verbatim and defers sufficiency to the P3 compliance engine.

## Entire Continuous Memory (Neathacks 2026)

TenderOps ships an offline-first Entire integration — Best Use of Entire entry:

- Auto checkpoint per supervised run (`OrchestratorService.run_tender()`),
  API at `/api/entire/*`, UI at `/entire`.
- Before-you-build briefing, intent-vs-change reviewer, Graph drift detector,
  checkpoint-to-demo generator, regression test scout.
- Agent instructions: `AGENTS.md`, `CLAUDE.md`, `.entire/graph-agent.md`,
  `.entire/agent-guide.md`. Full guide: `ENTIRE.md`.

```bash
uv run python scripts/entire_brief.py --query "EMD exemption check"
uv run python scripts/entire_review.py --latest
uv run python scripts/entire_demo.py --latest
uv run python -m pytest tests/test_entire_memory.py -q
```
