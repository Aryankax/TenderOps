# TenderOps

An Agentic AI system for autonomous tender execution, built with LangGraph. Analyzes tender documents, maps company evidence to compliance requirements, identifies eligibility gaps, prepares bid documents, and independently verifies bid readiness.

## Structure

Standard `uv` project (no `backend/` / `frontend/` split):

```text
.
├── pyproject.toml       # project metadata + dependencies
├── uv.lock              # locked dependencies
├── .python-version      # pinned Python version
├── .env.example         # example env vars (copy to .env)
└── app/                 # main package
    ├── main.py          # FastAPI entrypoint
    ├── agents/          # LangGraph agents
    ├── api/             # API routes
    ├── models/          # Pydantic schemas
    ├── services/        # business logic
    └── tools/           # agent tools
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

## Running the application

Run the development server:

```bash
uv run uvicorn app.main:app --reload
```
