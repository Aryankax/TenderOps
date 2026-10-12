from contextlib import asynccontextmanager

import neatlogs
from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.companies import router as companies_router
from app.api.entire import router as entire_router
from app.api.events import router as events_router
from app.api.jobs import router as jobs_router
from app.api.pipeline import router as pipeline_router
from app.api.tender_analysis import router as tender_analysis_router
from app.core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    neatlogs.init(
        api_key=settings.NEATLOGS_API_KEY,
        endpoint="https://ingest.neatlogs.com",
        workflow_name="tender-analysis",
    )
    yield
    neatlogs.flush()
    neatlogs.shutdown()


app = FastAPI(
    title="TenderOps API",
    description=(
        "Two-agent tender pipeline: the Tender Analyst Agent extracts "
        "structured requirements from GeM tender PDFs, and the Evidence "
        "Agent maps each requirement against indexed company documents."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(tender_analysis_router)
app.include_router(pipeline_router)
app.include_router(entire_router)
app.include_router(jobs_router)
app.include_router(events_router)
app.include_router(auth_router)
app.include_router(companies_router)


@app.get("/health", summary="Health check")
def health_check():
    return {"status": "ok"}
