import os
import shutil

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.api.deps import get_current_user, require_company_access
from app.core.config import settings
from app.services import auth as auth_store
from app.services import jobs as job_store

router = APIRouter(prefix="/api/jobs", tags=["Jobs"])


@router.post(
    "",
    status_code=202,
    summary="Start async pipeline run",
    description=(
        "Stores the tender PDF and runs the full supervised pipeline "
        "(P1+P2+P3+P4) in a background worker. Poll GET /api/jobs/{job_id} "
        "for the outcome — no long-lived HTTP connection needed."
    ),
    responses={
        400: {"description": "Not a PDF or file too large."},
    },
)
async def start_job(
    file: UploadFile = File(...),
    fetch_links: bool = Query(default=True),
    company_id: str = Query(
        default="sample",
        description="Company profile: evidence is searched strictly inside this company's index.",
    ),
    user: dict = Depends(get_current_user),
):
    company_id = require_company_access(user, company_id)
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    job_id = job_store.create_job(file.filename or "upload.pdf", company_id)
    dest = job_store.upload_path(job_id)
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    if os.path.getsize(dest) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        os.remove(dest)
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds maximum allowed size of {settings.MAX_UPLOAD_SIZE_MB}MB.",
        )
    job_store.submit_job(
        job_id, file.filename or "upload.pdf", company_id, fetch_links
    )
    return {"job_id": job_id, "status": "QUEUED", "company_id": company_id}


@router.get(
    "",
    summary="List recent jobs",
    responses={200: {"description": "Newest first."}},
)
async def list_jobs(
    limit: int = Query(default=20, le=100),
    user: dict = Depends(get_current_user),
):
    allowed = {c["slug"] for c in auth_store.list_companies(user["id"])}
    jobs = [
        j for j in job_store.list_jobs(limit * 5)
        if j.get("company_id") in allowed
    ]
    return {"jobs": jobs[: max(1, min(limit, 100))]}


@router.get(
    "/{job_id}",
    summary="Poll job status / result",
    responses={
        200: {"description": "Includes result once DONE."},
        404: {"description": "Unknown job_id."},
    },
)
async def get_job(job_id: str, user: dict = Depends(get_current_user)):
    job = job_store.get_job(job_id)
    if job is None or not auth_store.user_owns_slug(
        user["id"], job.get("company_id", "")
    ):
        raise HTTPException(status_code=404, detail=f"Unknown job {job_id}.")
    return job
