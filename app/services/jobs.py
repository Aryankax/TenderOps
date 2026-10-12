"""Async pipeline jobs: SQLite-backed, thread workers, pollable.

Long runs (5-10 min) must not hold an HTTP connection open: POST /api/jobs
stores the upload and returns immediately; GET /api/jobs/{id} polls the
outcome. SQLite + short-lived connections keep this thread-safe without
any new infrastructure.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from app.core.config import settings
from app.services.observability import observability

_EXECUTOR = ThreadPoolExecutor(max_workers=2)


def _db_path() -> str:
    path = getattr(settings, "JOBS_DB_PATH", "./data/jobs.sqlite")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=30)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS jobs ("
        "id TEXT PRIMARY KEY, tender_id TEXT, document_name TEXT, "
        "status TEXT, created_at TEXT, updated_at TEXT, "
        "result_json TEXT, error TEXT, company_id TEXT DEFAULT 'sample')"
    )
    # Migrate pre-company databases.
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN company_id TEXT DEFAULT 'sample'")
    except sqlite3.OperationalError:
        pass  # column already exists
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(document_name: str, company_id: str = "sample") -> str:
    from app.agents.evidence_agent.retrieval import sanitize_company_id

    job_id = f"job-{uuid.uuid4().hex[:12]}"
    with _connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, tender_id, document_name, status, "
            "created_at, updated_at, result_json, error, company_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, "", document_name, "QUEUED", _now(), _now(), "", "",
             sanitize_company_id(company_id)),
        )
    return job_id


def set_running(job_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status='RUNNING', updated_at=? WHERE id=?",
            (_now(), job_id),
        )


def store_result(job_id: str, tender_id: str, result: dict) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status='DONE', tender_id=?, updated_at=?, "
            "result_json=? WHERE id=?",
            (tender_id, _now(), json.dumps(result, ensure_ascii=False), job_id),
        )


def store_error(job_id: str, error: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status='FAILED', updated_at=?, error=? WHERE id=?",
            (_now(), error[:2000], job_id),
        )


def get_job(job_id: str) -> dict | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    if out.get("result_json"):
        try:
            out["result"] = json.loads(out["result_json"])
        except json.JSONDecodeError:
            out["result"] = None
    out.pop("result_json", None)
    return out


def list_jobs(limit: int = 20) -> list[dict]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, tender_id, document_name, status, created_at, "
            "updated_at, error, company_id FROM jobs ORDER BY created_at DESC LIMIT ?",
            (max(1, min(limit, 100)),),
        ).fetchall()
    return [dict(r) for r in rows]


def upload_path(job_id: str) -> str:
    base = getattr(settings, "UPLOAD_DIR", "./data/uploads")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, f"{job_id}.pdf")


def resume_upload_dir(job_id: str) -> str:
    """Per-job inbox for resume fix documents (multi-file, incl. zip members)."""
    base = getattr(settings, "UPLOAD_DIR", "./data/uploads")
    path = os.path.join(base, job_id)
    os.makedirs(path, exist_ok=True)
    return path


def create_resume_job(tender_id: str, company_id: str = "sample") -> str:
    from app.agents.evidence_agent.retrieval import sanitize_company_id

    job_id = f"job-{uuid.uuid4().hex[:12]}"
    with _connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, tender_id, document_name, status, "
            "created_at, updated_at, result_json, error, company_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, tender_id, f"resume:{tender_id}", "QUEUED", _now(),
             _now(), "", "", sanitize_company_id(company_id)),
        )
    return job_id


def run_resume_sync(
    job_id: str,
    tender_id: str,
    company_profile_id: str = "sample",
    waived_blocker_ids: list[str] | None = None,
    fix_map: dict[str, list[str]] | None = None,
) -> None:
    """Execute one resume inline (worker threads call this; tests call it too).

    Fix PDFs were staged into resume_upload_dir(job_id) by the endpoint;
    they are appended to the company evidence index and affected checks
    re-run. The stored result is PipelineResult-shaped so clients can merge
    it exactly like a sync /resume response.
    """
    import shutil as _shutil

    from app.agents.compliance_agent.service import ComplianceService
    from app.agents.evidence_agent.retrieval import sanitize_company_id

    stored = get_job(job_id) or {}
    company_profile_id = sanitize_company_id(
        stored.get("company_id") or company_profile_id
    )
    tender_id = stored.get("tender_id") or tender_id
    set_running(job_id)
    inbox = resume_upload_dir(job_id)
    try:
        names = sorted(
            f for f in os.listdir(inbox) if f.lower().endswith(".pdf")
        )
        new_docs = (
            [{"path": os.path.join(inbox, n), "name": n} for n in names]
            or None
        )
        resumed = ComplianceService.resume_pipeline(
            tender_id,
            new_docs=new_docs,
            company_profile_id=company_profile_id,
            waived_blocker_ids=waived_blocker_ids or None,
            fix_map=fix_map or None,
        )
        analysis = resumed["analysis"]
        compliance = resumed["compliance"]
        from app.models.pipeline import PipelineResult

        result = PipelineResult(
            tender_id=analysis.tender_id,
            analysis=analysis,
            evidence=resumed["evidence"],
            compliance=compliance,
            package=resumed.get("package"),
            verification=resumed.get("verification"),
            pipeline_status=(
                "PAUSED_ON_BLOCKERS"
                if compliance.decision == "PAUSE"
                else "COMPLETED"
            ),
            checkpoint_path=ComplianceService.checkpoint_path(
                analysis.tender_id
            ),
        ).model_dump()
        store_result(job_id, str(analysis.tender_id), result)
        observability.log_event(
            "job_completed", job_id=job_id, kind="resume", tender_id=tender_id
        )
    except Exception as e:  # noqa: BLE001 — job failure must persist, not raise
        store_error(job_id, str(e)[:2000])
        observability.log_error("job_failed", e, job_id=job_id, kind="resume")
    finally:
        try:
            _shutil.rmtree(inbox, ignore_errors=True)
        except OSError:
            pass


def submit_resume_job(
    job_id: str,
    tender_id: str,
    company_profile_id: str = "sample",
    waived_blocker_ids: list[str] | None = None,
    fix_map: dict[str, list[str]] | None = None,
):
    from app.agents.evidence_agent.retrieval import sanitize_company_id

    return _EXECUTOR.submit(
        run_resume_sync, job_id, tender_id,
        sanitize_company_id(company_profile_id), waived_blocker_ids or None,
        fix_map or None,
    )


def run_job_sync(
    job_id: str,
    document_name: str,
    company_profile_id: str = "sample",
    fetch_links: bool = True,
) -> None:
    """Execute one job inline (worker threads call this; tests call it too)."""
    from app.agents.evidence_agent.retrieval import sanitize_company_id
    from app.orchestrator.service import OrchestratorService

    stored = get_job(job_id) or {}
    # The DB row is the source of truth: uploads from another company can
    # never leak into this run even if callers disagree.
    company_profile_id = sanitize_company_id(
        stored.get("company_id") or company_profile_id
    )
    set_running(job_id)
    pdf_path = upload_path(job_id)
    try:
        out = OrchestratorService.run_tender(
            pdf_path, document_name, company_profile_id, fetch_links
        )
        store_result(job_id, str(out.get("tender_id", "")), out)
        observability.log_event("job_completed", job_id=job_id)
    except Exception as e:  # noqa: BLE001 — job failure must persist, not raise
        store_error(job_id, str(e)[:2000])
        observability.log_error("job_failed", e, job_id=job_id)
    finally:
        try:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
        except OSError:
            pass


def submit_job(
    job_id: str,
    document_name: str,
    company_profile_id: str = "sample",
    fetch_links: bool = True,
):
    from app.agents.evidence_agent.retrieval import sanitize_company_id

    return _EXECUTOR.submit(
        run_job_sync, job_id, document_name,
        sanitize_company_id(company_profile_id), fetch_links,
    )
