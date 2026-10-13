"""Company workspace: create a company, manage its document index."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.agents.evidence_agent import retrieval
from app.api.deps import get_current_user, require_company_access
from app.services import auth as auth_store

router = APIRouter(prefix="/api/companies", tags=["Companies"])


class CreateCompany(BaseModel):
    name: str


@router.get("", summary="My companies")
async def my_companies(user: dict[str, Any] = Depends(get_current_user)):
    return {"companies": auth_store.list_companies(user["id"])}


@router.post(
    "",
    status_code=201,
    summary="Create a company workspace",
    responses={400: {"description": "Invalid name."}},
)
async def create_company(
    payload: CreateCompany, user: dict[str, Any] = Depends(get_current_user)
):
    try:
        company = auth_store.create_company(user["id"], payload.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return company


@router.get("/{slug}", summary="Company + indexed document inventory")
async def company_detail(slug: str, user: dict[str, Any] = Depends(get_current_user)):
    clean = require_company_access(user, slug)
    company = auth_store.get_company_by_slug(clean)
    return {
        "company": auth_store.public_company(company or {}),
        "index": retrieval.list_indexed_documents(clean),
    }


@router.get("/{slug}/documents", summary="Indexed documents for a company")
async def company_documents(
    slug: str, user: dict[str, Any] = Depends(get_current_user)
):
    clean = require_company_access(user, slug)
    return retrieval.list_indexed_documents(clean)


@router.post(
    "/{slug}/documents",
    summary="Upload PDFs/zips into a company's index",
    responses={
        400: {"description": "No usable PDFs."},
        403: {"description": "Not your company."},
    },
)
async def upload_company_documents(
    slug: str,
    user: dict[str, Any] = Depends(get_current_user),
    files: list[UploadFile] | None = File(default=None),
    zip_file: UploadFile | str | None = File(default=None),
):
    """Append (never wipe) company documents to that company's index."""
    from app.api import pipeline as pipeline_mod

    clean = require_company_access(user, slug)
    saved: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    temp_paths: list[str] = []
    try:
        for upload in files or []:
            name = (upload.filename or "").strip()
            if not name:
                continue
            if not name.lower().endswith(".pdf"):
                raise HTTPException(
                    status_code=400, detail="Only PDF files are supported."
                )
            path = pipeline_mod._save_upload(upload)
            temp_paths.append(path)
            reason = pipeline_mod._light_validate_pdf(path)
            if reason:
                skipped.append({"name": name, "reason": reason})
                continue
            saved.append({"path": path, "name": name})
        if isinstance(zip_file, str) or (
            zip_file is not None
            and not (zip_file.filename or getattr(zip_file, "size", 0))
        ):
            zip_file = None
        if zip_file is not None:
            if not (zip_file.filename or "").lower().endswith(".zip"):
                raise HTTPException(
                    status_code=400, detail="zip_file must be a .zip archive."
                )
            for entry in pipeline_mod._extract_pdfs_from_zip(zip_file, temp_paths):
                if "path" in entry:
                    saved.append({"path": entry["path"], "name": entry["name"]})
                else:
                    skipped.append({"name": entry["name"], "reason": entry["reason"]})
        if not saved:
            detail = "No usable PDFs provided."
            if skipped:
                detail += " Skipped: " + "; ".join(
                    f"{s['name']} ({s['reason']})" for s in skipped[:10]
                )
            raise HTTPException(status_code=400, detail=detail)
        stats = retrieval.index_company_docs(saved, clean, replace=False)
        return {
            "company_profile_id": clean,
            **stats,
            "indexed_files": [s["name"] for s in saved],
            "skipped": skipped,
        }
    finally:
        for path in temp_paths:
            if os.path.exists(path):
                os.remove(path)
