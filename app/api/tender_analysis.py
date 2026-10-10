import os
import shutil
import tempfile

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.agents.tender_analyst.service import TenderAnalystService
from app.core.config import settings
from app.models.tender import TenderAnalysisResult

router = APIRouter(prefix="/api/tender-analysis", tags=["Tender Analysis"])

import neatlogs


@router.post("", response_model=TenderAnalysisResult)
@neatlogs.span(kind="WORKFLOW")
async def analyze_tender(file: UploadFile = File(...)):
    document_name = file.filename or "upload.pdf"
    if not document_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

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
            result = TenderAnalystService.analyze_tender(
                temp_path, document_name=document_name
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
