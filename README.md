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

The main Tender Analysis endpoint is:
`POST /api/tender-analysis`

Upload a PDF file using `multipart/form-data` with the key `file`.

## Observability

We use an isolated adapter in `app/services/observability.py` for `neatlogs`. The actual integration will be added once the `neatlogs` API can be verified.
