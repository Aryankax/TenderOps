# TenderOps Backend

An agentic AI system for autonomous tender analysis and bid preparation.

## Setup

1. Install [uv](https://docs.astral.sh/uv/) if you haven't already.
2. Navigate to the `backend` directory.
3. Install dependencies:
   ```bash
   uv sync
   ```
4. Copy `.env.example` to `.env` and fill in your credentials:
   ```bash
   cp .env.example .env
   ```

## Running the application

Run the development server:
```bash
uv run uvicorn app.main:app --reload
```
