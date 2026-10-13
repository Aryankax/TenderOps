import asyncio
import contextlib
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import neatlogs
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)

from app.agents.compliance_agent.service import ComplianceService
from app.agents.evidence_agent import retrieval
from app.api.deps import get_current_user, require_company_access
from app.core.config import settings
from app.models.pipeline import PipelineResult
from app.services import jobs as job_store

router = APIRouter(prefix="/api/pipeline", tags=["Pipeline"])

_DEFAULT_PROFILE = "sample"


def _save_upload(upload: UploadFile, suffix: str = ".pdf") -> str:
    fd, temp_path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        shutil.copyfileobj(upload.file, f)
    return temp_path


@router.post(
    "/run",
    response_model=PipelineResult,
    summary="Run full pipeline (P1+P2+P3+P4)",
    description=(
        "Upload a tender PDF: supervised run of tender analysis (P1, incl. "
        "linked annexures), evidence mapping (P2), compliance + blockers "
        "(P3), then bid drafting with independent verification (P4, bounded "
        "fix loop). Pauses with a checkpoint when blockers need human "
        "fixes; resume via POST /api/pipeline/resume or run async via jobs."
    ),
    responses={
        400: {"description": "Not a PDF, file too large, or PDF unparseable."},
        500: {"description": "Analysis, evidence-mapping or compliance failure."},
        404: {"description": "Unknown tender_id (resume only)."},
    },
)
@neatlogs.span(kind="WORKFLOW")
async def run_pipeline(
    file: UploadFile = File(...),
    fetch_links: bool = Query(
        default=True,
        description="Follow embedded links/attachments (annexures) into the analysis.",
    ),
    company_id: str = Query(
        default="sample",
        description="Company profile: evidence is searched strictly inside this company's index.",
    ),
    user: dict[str, Any] = Depends(get_current_user),
):
    """P1+P2+P3 wired: analyze, map evidence, assess compliance/blockers."""
    company_id = require_company_access(user, company_id)
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    document_name = file.filename or "upload.pdf"
    temp_path = _save_upload(file)
    try:
        if os.path.getsize(temp_path) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
            raise HTTPException(
                status_code=400,
                detail=f"File exceeds maximum allowed size of "
                f"{settings.MAX_UPLOAD_SIZE_MB}MB.",
            )
        try:
            from app.agents.evidence_agent.retrieval import sanitize_company_id
            from app.orchestrator.service import OrchestratorService

            # Offload the minutes-long blocking LangGraph run to a worker
            # thread so the uvicorn event loop stays free for /api/events/stream
            # and other requests. Otherwise the loop stalls and the Next.js
            # rewrite proxy logs ECONNRESET / socket hang up.
            out = await asyncio.to_thread(
                OrchestratorService.run_tender,
                temp_path,
                document_name=document_name,
                company_profile_id=sanitize_company_id(company_id),
                fetch_links=fetch_links,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except RuntimeError:
            raise HTTPException(
                status_code=500, detail="Internal server error during pipeline."
            )
        return PipelineResult(
            tender_id=out["tender_id"],
            analysis=out["analysis"],
            evidence=out["evidence"],
            compliance=out.get("compliance"),
            package=out.get("package"),
            verification=out.get("verification"),
            pipeline_status=out.get("pipeline_status", "COMPLETED"),
            checkpoint_path=out.get("checkpoint_path"),
        )
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


@router.post(
    "/resume",
    response_model=PipelineResult,
    summary="Resume a paused pipeline",
    description=(
        "Reload a checkpoint by tender_id, optionally with additional company "
        "PDFs (appended to the evidence index), then re-run only affected "
        "checks (missing/review/partial requirements + blocker-linked ones) "
        "before a fresh compliance pass."
    ),
    responses={
        400: {"description": "No usable PDFs in the fix upload."},
        404: {"description": "Unknown tender_id (no checkpoint)."},
        500: {"description": "Resume failure."},
    },
)
@neatlogs.span(kind="WORKFLOW")
async def resume_pipeline(
    tender_id: str = Form(...),
    files: list[UploadFile] | None = File(default=None),
    zip_file: UploadFile | str | None = File(default=None),
    company_id: str = Form(default="sample"),
    waive: list[str] = Form(default=[]),
    fix_map: str = Form(
        default="",
        description="JSON {blocker_id: [uploaded filenames]}: each blocker's "
        "requirements are re-checked ONLY against its own fix files.",
    ),
    user: dict[str, Any] = Depends(get_current_user),
):
    """P3 resume: append fix documents, re-run affected checks, reassess."""

    company_id = require_company_access(user, company_id)

    temp_paths: list[str] = []
    new_docs: list[dict[str, str]] = []
    staged: list[tuple[str, str]] = []  # (original filename, indexed name)
    try:
        for upload in files or []:
            name = (upload.filename or "").strip()
            if not name:
                if getattr(upload, "size", 0):
                    raise HTTPException(
                        status_code=400, detail="Only PDF files are supported."
                    )
                continue
            if not name.lower().endswith(".pdf"):
                raise HTTPException(
                    status_code=400, detail="Only PDF files are supported."
                )
            path = _save_upload(upload)
            temp_paths.append(path)
            reason = _light_validate_pdf(path)
            if reason:
                raise HTTPException(status_code=400, detail=f"{name}: {reason}.")
            new_docs.append({"path": path, "name": name})
            staged.append((name, name))
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
            for entry in _extract_pdfs_from_zip(zip_file, temp_paths):
                if "path" in entry:
                    new_docs.append({"path": entry["path"], "name": entry["name"]})
                    staged.append((entry.get("original", entry["name"]), entry["name"]))
        try:
            from app.agents.evidence_agent.retrieval import sanitize_company_id

            waived_ids = [
                part.strip()
                for entry in (waive or [])
                for part in str(entry).split(",")
                if part.strip()
            ]
            scoped = _translate_fix_map(_parse_fix_map(fix_map), staged)
            resumed = await asyncio.to_thread(
                ComplianceService.resume_pipeline,
                tender_id,
                new_docs=new_docs or None,
                company_profile_id=sanitize_company_id(company_id),
                waived_blocker_ids=waived_ids or None,
                fix_map=scoped or None,
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=404, detail=f"No checkpoint for tender {tender_id}."
            )
        except HTTPException:
            raise
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=f"Resume failed: {e!s}"[:1000])
        except Exception as e:  # noqa: BLE001 — surface real cause, not opaque 500
            import logging as _logging

            _logging.getLogger(__name__).exception(
                "resume failed for tender %s", tender_id
            )
            raise HTTPException(status_code=500, detail=f"Resume failed: {e!s}"[:1000])
        analysis = resumed["analysis"]
        compliance = resumed["compliance"]
        return PipelineResult(
            tender_id=analysis.tender_id,
            analysis=analysis,
            evidence=resumed["evidence"],
            compliance=compliance,
            package=resumed.get("package"),
            verification=resumed.get("verification"),
            pipeline_status=(
                "PAUSED_ON_BLOCKERS" if compliance.decision == "PAUSE" else "COMPLETED"
            ),
            checkpoint_path=ComplianceService.checkpoint_path(analysis.tender_id),
        )
    finally:
        for path in temp_paths:
            if os.path.exists(path):
                os.remove(path)


@router.post(
    "/resume-job",
    status_code=202,
    summary="Resume a paused pipeline in the background",
    description=(
        "Same as POST /api/pipeline/resume, but returns immediately with a "
        "job_id: fix documents are staged and the re-mapping + compliance "
        "pass runs in a worker thread. Poll GET /api/jobs/{job_id} for the "
        "outcome — no long-lived HTTP connection, so proxies never time out. "
        "Use this from the UI instead of /resume for real (multi-minute) runs."
    ),
    responses={
        400: {"description": "No usable PDFs in the fix upload."},
        404: {"description": "Unknown tender_id (no checkpoint)."},
    },
)
async def resume_job(
    tender_id: str = Form(...),
    files: list[UploadFile] | None = File(default=None),
    zip_file: UploadFile | str | None = File(default=None),
    company_id: str = Form(default="sample"),
    waive: list[str] = Form(default=[]),
    fix_map: str = Form(
        default="",
        description="JSON {blocker_id: [uploaded filenames]}: each blocker's "
        "requirements are re-checked ONLY against its own fix files.",
    ),
    user: dict[str, Any] = Depends(get_current_user),
):
    """Enqueue a resume; fix docs + waivers are picked up by the worker."""
    company_id = require_company_access(user, company_id)
    # Fail fast on unknown tenders instead of enqueueing a doomed job.
    if not os.path.isfile(ComplianceService.checkpoint_path(tender_id)):
        raise HTTPException(
            status_code=404, detail=f"No checkpoint for tender {tender_id}."
        )
    waived_ids = [
        part.strip()
        for entry in (waive or [])
        for part in str(entry).split(",")
        if part.strip()
    ]
    temp_paths: list[str] = []
    staged: list[dict[str, str]] = []
    try:
        for upload in files or []:
            name = (upload.filename or "").strip()
            if not name:
                if getattr(upload, "size", 0):
                    raise HTTPException(
                        status_code=400, detail="Only PDF files are supported."
                    )
                continue
            if not name.lower().endswith(".pdf"):
                raise HTTPException(
                    status_code=400, detail="Only PDF files are supported."
                )
            path = _save_upload(upload)
            temp_paths.append(path)
            reason = _light_validate_pdf(path)
            if reason:
                raise HTTPException(status_code=400, detail=f"{name}: {reason}.")
            staged.append({"path": path, "name": name, "original": name})
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
            for entry in _extract_pdfs_from_zip(zip_file, temp_paths):
                if "path" in entry:
                    staged.append(
                        {
                            "path": entry["path"],
                            "name": entry["name"],
                            "original": entry.get("original", entry["name"]),
                        }
                    )
        job_id = job_store.create_resume_job(tender_id, company_id)
        inbox = job_store.resume_upload_dir(job_id)
        staged_pairs: list[tuple[str, str]] = []
        for doc in staged:
            dest = os.path.join(inbox, doc["name"])
            if os.path.exists(dest):  # same name twice (files + zip)
                stem, suffix = os.path.splitext(doc["name"])
                n = 2
                while os.path.exists(os.path.join(inbox, f"{stem}_{n}{suffix}")):
                    n += 1
                dest = os.path.join(inbox, f"{stem}_{n}{suffix}")
            shutil.move(doc["path"], dest)
            with contextlib.suppress(ValueError):
                temp_paths.remove(doc["path"])
            staged_pairs.append((doc["original"], os.path.basename(dest)))
        scoped = _translate_fix_map(_parse_fix_map(fix_map), staged_pairs)
        job_store.submit_resume_job(
            job_id, tender_id, company_id, waived_ids, scoped or None
        )
        return {"job_id": job_id, "status": "QUEUED", "tender_id": tender_id}
    finally:
        for path in temp_paths:
            if os.path.exists(path):
                os.remove(path)


@router.get(
    "/evidence-index",
    summary="List indexed company documents",
    description=(
        "Inventory of a company's evidence index: which documents are "
        "indexed, with chunk and page counts. Use company_id to inspect one "
        "company's private index."
    ),
    responses={200: {"description": "Empty files list when nothing indexed."}},
)
async def list_evidence_index(
    company_id: str = Query(default="sample"),
    user: dict[str, Any] = Depends(get_current_user),
):
    company_id = require_company_access(user, company_id)
    return retrieval.list_indexed_documents(company_id)


@router.post(
    "/evidence-index",
    summary="Rebuild company evidence index",
    description=(
        "Upload company PDFs (turnover/experience/OEM certificates, …) either "
        "as individual `files` or as a single `zip_file` containing many PDFs "
        "(the zip is unpacked server-side, non-PDF members skipped). "
        "Replaces the 'sample' evidence collection used by P2 mapping."
    ),
    responses={
        400: {"description": "No usable PDFs (wrong type, bad zip, or all skipped)."},
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "files": {
                                "type": "array",
                                "items": {"type": "string", "format": "binary"},
                                "description": "Individual company PDFs (repeatable).",
                            },
                            "zip_file": {
                                "type": "string",
                                "format": "binary",
                                "description": "Zip archive of company PDFs.",
                            },
                        },
                    }
                }
            }
        }
    },
)
async def rebuild_evidence_index(
    request: Request, user: dict[str, Any] = Depends(get_current_user)
):
    """Replace the company evidence index with uploaded PDFs."""
    try:
        form = await request.form()
    except Exception:
        raise HTTPException(status_code=400, detail="Expected multipart form data.")
    raw_company = form.get("company_id", "sample")
    company_id = require_company_access(
        user, raw_company if isinstance(raw_company, str) else "sample"
    )
    files = _coerce_uploads(form.getlist("files"))
    zip_candidates = _coerce_uploads(form.getlist("zip_file"))
    zip_file = zip_candidates[0] if zip_candidates else None
    saved: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    temp_paths: list[str] = []
    try:
        for upload in files:
            name = (upload.filename or "").strip()
            if not name:
                if getattr(upload, "size", 0):
                    raise HTTPException(
                        status_code=400, detail="Only PDF files are supported."
                    )
                continue  # untouched file box submitted empty
            if not name.lower().endswith(".pdf"):
                raise HTTPException(
                    status_code=400, detail="Only PDF files are supported."
                )
            path = _save_upload(upload)
            temp_paths.append(path)
            reason = _light_validate_pdf(path)
            if reason:
                skipped.append({"name": name, "reason": reason})
                continue
            saved.append({"path": path, "name": name})
        if zip_file is not None:
            for entry in _extract_pdfs_from_zip(zip_file, temp_paths):
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
        stats = retrieval.index_company_docs(saved, company_id)
        return {
            "company_profile_id": company_id,
            **stats,
            "indexed_files": [s["name"] for s in saved],
            "skipped": skipped,
        }
    finally:
        for path in temp_paths:
            if os.path.exists(path):
                os.remove(path)


def _is_upload(value) -> bool:
    """Duck-typed file check: works for Starlette AND FastAPI UploadFile.

    (These are different classes in current versions, so isinstance against
    one of them is unreliable.)
    """
    return (
        not isinstance(value, (str, bytes))
        and hasattr(value, "filename")
        and hasattr(value, "file")
    )


def _coerce_uploads(values: Any) -> list[Any]:
    """Drop empty Swagger submissions (blank strings / nameless empty parts)."""
    out = []
    for value in values or []:
        if not _is_upload(value):
            continue
        if not (value.filename or getattr(value, "size", 0)):
            continue
        out.append(value)
    return out


def _parse_fix_map(raw: str | None) -> dict[str, list[str]]:
    """Parse the fix_map form field: {blocker_id: [original filenames]}."""
    if not raw or not str(raw).strip():
        return {}
    try:
        import json as _json

        data = _json.loads(str(raw))
    except Exception:
        raise HTTPException(status_code=400, detail="fix_map must be a JSON object.")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="fix_map must be a JSON object.")
    out: dict[str, list[str]] = {}
    for key, value in data.items():
        names = value if isinstance(value, list) else [value]
        clean = [str(n).strip() for n in names if str(n).strip()]
        if clean:
            out[str(key).strip()] = clean
    return out


def _translate_fix_map(
    fix_map: dict[str, list[str]],
    staged: list[tuple[str, str]],
) -> dict[str, list[str]]:
    """Map client-side original filenames to indexed names.

    staged is [(original, indexed)] in staging order (zip dedup renames and
    inbox-collision renames change the indexed name). Duplicate originals
    consume staged pairs in order. Unknown names are dropped with an event —
    the staged files list stays the source of truth for what was indexed.
    """
    from app.services.observability import observability

    remaining = list(staged)
    translated: dict[str, list[str]] = {}
    for blocker_id, names in fix_map.items():
        mapped: list[str] = []
        for name in names:
            hit = next((p for p in remaining if p[0] == name), None)
            if hit is None:
                hit = next((p for p in remaining if p[1] == name), None)
            if hit is None:
                observability.log_event(
                    "resume_fix_unmatched", blocker_id=blocker_id, filename=name
                )
                continue
            remaining.remove(hit)
            mapped.append(hit[1])
        if mapped:
            translated[blocker_id] = mapped
    return translated


def _light_validate_pdf(path: str) -> str | None:
    """Cheap open-and-count check; returns a skip reason or None if usable."""
    try:
        import pymupdf as fitz

        with fitz.open(path) as doc:
            if doc.page_count == 0:
                return "PDF has no pages"
    except Exception as e:
        return f"unreadable PDF ({str(e)[:100]})"
    return None


def _extract_pdfs_from_zip(
    zip_file: UploadFile, temp_paths: list[str]
) -> list[dict[str, str]]:
    """Unpack a zip upload into temp PDFs, one by one.

    Returns a mix of {"path", "name"} (extracted PDF) and {"name", "reason"}
    (skipped member) dicts. Zip-slip safe: member paths are flattened to
    basenames and traversal entries rejected; member count and total
    uncompressed size are capped against settings.
    """
    fd, zip_path = tempfile.mkstemp(suffix=".zip")
    temp_paths.append(zip_path)
    with os.fdopen(fd, "wb") as f:
        shutil.copyfileobj(zip_file.file, f)
    if os.path.getsize(zip_path) > settings.EVIDENCE_MAX_ZIP_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail=f"Zip exceeds maximum allowed size of {settings.EVIDENCE_MAX_ZIP_SIZE_MB}MB.",
        )
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise HTTPException(
            status_code=400, detail="zip_file is not a valid zip archive."
        )
    entries: list[dict[str, str]] = []
    with archive:
        members = [m for m in archive.infolist() if not m.is_dir()]
        if len(members) > settings.EVIDENCE_MAX_ZIP_FILES:
            raise HTTPException(
                status_code=400,
                detail=f"Zip contains {len(members)} files (max {settings.EVIDENCE_MAX_ZIP_FILES}).",
            )
        total_uncompressed = sum(m.file_size for m in members)
        if total_uncompressed > settings.EVIDENCE_MAX_ZIP_SIZE_MB * 10 * 1024 * 1024:
            raise HTTPException(
                status_code=400, detail="Zip contents suspiciously large — rejected."
            )
        seen_names: set[str] = set()
        for member in members:
            name = Path(member.filename).name.strip()
            if (
                not name
                or name.startswith(".")
                or ".." in Path(member.filename).parts
                or Path(member.filename).is_absolute()
            ):
                entries.append(
                    {"name": member.filename or "?", "reason": "unsafe path"}
                )
                continue
            if not name.lower().endswith(".pdf"):
                entries.append({"name": name, "reason": "not a PDF"})
                continue
            if name in seen_names:  # flatten collisions across folders
                stem, suffix = os.path.splitext(name)
                n = 2
                while f"{stem}_{n}{suffix}" in seen_names:
                    n += 1
                name = f"{stem}_{n}{suffix}"
            seen_names.add(name)
            fd, temp_path = tempfile.mkstemp(suffix=".pdf")
            temp_paths.append(temp_path)
            with os.fdopen(fd, "wb") as f, archive.open(member) as src:
                shutil.copyfileobj(src, f)
            reason = _light_validate_pdf(temp_path)
            if reason:
                entries.append({"name": name, "reason": reason})
                continue
            entries.append(
                {
                    "path": temp_path,
                    "name": name,
                    "original": Path(member.filename).name.strip(),
                }
            )
    return entries
