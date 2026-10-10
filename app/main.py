import os
import neatlogs
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api.tender_analysis import router as tender_analysis_router

from app.core.config import settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    neatlogs.init(
        api_key=settings.NEATLOGS_API_KEY,
        endpoint="https://ingest.neatlogs.com",
        workflow_name="tender-analysis"
    )
    yield
    neatlogs.flush()
    neatlogs.shutdown()

app = FastAPI(title="TenderOps API", lifespan=lifespan)

app.include_router(tender_analysis_router)

@app.get("/health")
def health_check():
    return {"status": "ok"}
