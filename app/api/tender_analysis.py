import asyncio
import os
import shutil
import tempfile

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.api.deps import get_current_user

from app.agents.tender_analyst.service import TenderAnalystService
from app.core.config import settings
from app.models.tender import TenderAnalysisResult

router = APIRouter(prefix="/api/tender-analysis", tags=["Tender Analysis"])

import neatlogs


@router.post(
    "",
    response_model=TenderAnalysisResult,
    summary="Analyze tender PDF",
    description=(
        "Upload a GeM tender PDF (max 10 MB). Returns structured metadata, "
        "per-chunk requirements with normalized thresholds, cross-chunk "
        "issues (missing attachments, contradictions), and review flags."
    ),
    responses={
        400: {"description": "Not a PDF, file too large, or PDF unparseable."},
        500: {"description": "Analysis pipeline failure."},
    },
)
@neatlogs.span(kind="WORKFLOW")
async def analyze_tender(
    file: UploadFile = File(...),
    fetch_links: bool = Query(
        default=True,
        description="Follow embedded links/attachments (annexures) into the analysis.",
    ),
    user: dict = Depends(get_current_user),
):
    """Run the Tender Analyst Agent (P1) on an uploaded tender PDF."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    document_name = file.filename or "upload.pdf"

    try:
        # Save uploaded file to temp directory
        fd, temp_path = tempfile.mkstemp(suffix=".pdf")
        with os.fdopen(fd, "wb") as f:
            shutil.copyfileobj(file.file, f)

        file_size = os.path.getsize(temp_path)
        if file_size > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
            os.remove(temp_path)
            raise HTTPException(
                status_code=400,
                detail=f"File exceeds maximum allowed size of {settings.MAX_UPLOAD_SIZE_MB}MB.",
            )

        try:
            # Same as pipeline/run: blocking LLM work must not stall the event loop.
            result = await asyncio.to_thread(
                TenderAnalystService.analyze_tender,
                temp_path, document_name=document_name, fetch_links=fetch_links
            )
            return result
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except RuntimeError:
            raise HTTPException(
                status_code=500, detail="Internal server error during analysis."
            )
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e!s}")
