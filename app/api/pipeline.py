import os
import shutil
import tempfile

import neatlogs
from fastapi import APIRouter, File, HTTPException, UploadFile

from app.agents.evidence_agent.service import EvidenceAgentService
from app.agents.tender_analyst.service import TenderAnalystService
from app.core.config import settings
from app.models.pipeline import PipelineResult

router = APIRouter(prefix="/api/pipeline", tags=["Pipeline"])

_DEFAULT_PROFILE = "sample"


def _save_upload(upload: UploadFile, suffix: str = ".pdf") -> str:
    fd, temp_path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        shutil.copyfileobj(upload.file, f)
    return temp_path


@router.post("/run", response_model=PipelineResult)
@neatlogs.span(kind="WORKFLOW")
async def run_pipeline(file: UploadFile = File(...)):
    """P1+P2 wired: analyze tender PDF, then map company evidence."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    temp_path = _save_upload(file)
    try:
        if os.path.getsize(temp_path) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
            raise HTTPException(
                status_code=400,
                detail=f"File exceeds maximum allowed size of "
                f"{settings.MAX_UPLOAD_SIZE_MB}MB.",
            )
        try:
            analysis = TenderAnalystService.analyze_tender(
                temp_path, document_name=file.filename
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except RuntimeError:
            raise HTTPException(
                status_code=500, detail="Internal server error during analysis."
            )
        try:
            evidence = EvidenceAgentService.map_evidence(
                analysis, company_profile_id=_DEFAULT_PROFILE
            )
        except RuntimeError:
            raise HTTPException(
                status_code=500,
                detail="Internal server error during evidence mapping.",
            )
        return PipelineResult(
            tender_id=analysis.tender_id, analysis=analysis, evidence=evidence
        )
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


@router.post("/evidence-index")
async def rebuild_evidence_index(files: list[UploadFile] = File(...)):
    """Replace the sample company evidence index with uploaded PDFs."""
    saved = []
    try:
        for upload in files:
            if not upload.filename.lower().endswith(".pdf"):
                raise HTTPException(
                    status_code=400, detail="Only PDF files are supported."
                )
            path = _save_upload(upload)
            saved.append({"path": path, "name": upload.filename})
        from app.agents.evidence_agent.retrieval import index_company_docs

        stats = index_company_docs(saved, _DEFAULT_PROFILE)
        return {"company_profile_id": _DEFAULT_PROFILE, **stats}
    finally:
        for doc in saved:
            if os.path.exists(doc["path"]):
                os.remove(doc["path"])
