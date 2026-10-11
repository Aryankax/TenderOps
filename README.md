# TenderOps

An Agentic AI system for autonomous tender execution, built with LangGraph. Analyzes tender documents, maps company evidence to compliance requirements, identifies eligibility gaps, prepares bid documents, and independently verifies bid readiness.

## Configuration

1. Copy `.env.example` to `.env` if not present.
2. Set your `GROQ_API_KEY` in `.env`.
3. Set `GROQ_MODEL` to your preferred model (e.g. `llama-3.1-70b-versatile`).

## Running the Application

To run the API server locally:

```bash
uv run uvicorn app.main:app --reload
```

## Running Tests

To execute the test suite (uses mocked LLM):

```bash
uv run python -m pytest tests/
```

## API Usage

The main endpoints are:

- `POST /api/tender-analysis` — P1: analyze a tender PDF.
- `POST /api/pipeline/run` — P1+P2 wired: tender PDF → requirements → evidence map.
- `POST /api/pipeline/evidence-index` — replace the company evidence index
  with uploaded PDFs (one per file field).

Upload a PDF file using `multipart/form-data` with the key `file`.

## Evidence agent (P2)

Retrieves company evidence per requirement and judges support level:

- Index: `data/sample-company/*.pdf` parsed page-wise into Chroma
  (`data/evidence_chroma/`, local MiniLM embeddings — no API spend).
  Rebuild fixtures with `uv run python scripts/make_sample_company_docs.py`.
- `EvidenceMap` statuses: `satisfied` | `partial` | `missing` | `needs_review`,
  each with confidence, source hits (doc + page + excerpt), and notes.
- Numbers rule: the mapper never converts units or compares figures —
  it quotes them verbatim and defers sufficiency to the P3 compliance engine.

## Observability

We use an isolated adapter in `app/services/observability.py` for `neatlogs`. The actual integration will be added once the `neatlogs` API can be verified.
