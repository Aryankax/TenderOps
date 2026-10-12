"""Company isolation: X vs Z indexes never mix."""

import pymupdf


def _pdf_bytes(text):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


def test_sanitize_company_id():
    from app.agents.evidence_agent.retrieval import sanitize_company_id

    assert sanitize_company_id("company-X_1") == "company-X_1"
    assert sanitize_company_id("../../etc") == "etc"
    assert sanitize_company_id("a;DROP TABLE") == "aDROPTABLE"
    assert sanitize_company_id("") == "sample"
    assert sanitize_company_id(None) == "sample"
    assert len(sanitize_company_id("x" * 200)) == 64


def test_indexes_are_isolated(tmp_path, monkeypatch):
    """Docs indexed under X are invisible to Z (real Chroma, tmp dir)."""
    from app.agents.evidence_agent import retrieval
    from app.core import config as config_mod

    monkeypatch.setattr(
        config_mod.settings, "EVIDENCE_CHROMA_DIR", str(tmp_path / "chroma")
    )
    pdf = tmp_path / "turnover.pdf"
    pdf.write_bytes(_pdf_bytes("Annual turnover Rs. 12 crore certified"))

    stats = retrieval.index_company_docs(
        [{"path": str(pdf), "name": "turnover.pdf"}], "company-X"
    )
    assert stats["documents"] == 1

    hits_z = retrieval.query_evidence("annual turnover", "company-Z")
    assert hits_z == []
    hits_x = retrieval.query_evidence("annual turnover", "company-X")
    assert len(hits_x) == 1
    assert hits_x[0]["document_name"] == "turnover.pdf"

    # Injection-style profile cannot escape its namespace.
    hits_evil = retrieval.query_evidence("annual turnover", "../..")
    assert hits_evil == []


def test_job_uses_db_company_not_caller_arg(tmp_path, monkeypatch):
    from app.core import config as config_mod
    monkeypatch.setattr(config_mod.settings, "JOBS_DB_PATH", str(tmp_path / "j.sqlite"))
    monkeypatch.setattr(config_mod.settings, "UPLOAD_DIR", str(tmp_path / "up"))
    from app.services import jobs as J
    import app.orchestrator.service as OS

    seen = {}
    monkeypatch.setattr(
        OS.OrchestratorService, "run_tender",
        staticmethod(lambda *a, **k: seen.update(
            profile=k.get("company_profile_id", a[2] if len(a) > 2 else None)
        ) or {"tender_id": "t"}),
    )
    jid = J.create_job("d.pdf", "company-Z")
    open(J.upload_path(jid), "wb").write(b"%PDF-1.4 fake")
    # Caller passes a DIFFERENT profile: DB row must win.
    J.run_job_sync(jid, "d.pdf", "company-X", True)
    assert seen["profile"] == "company-Z"
    assert J.get_job(jid)["company_id"] == "company-Z"


def test_evidence_index_endpoint_scopes_company(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import app.api.pipeline as pipeline_mod
    import app.main as app_main
    from helpers import isolate_auth, login_headers, make_company

    isolate_auth(monkeypatch, tmp_path)
    seen = {}
    monkeypatch.setattr(
        pipeline_mod.retrieval, "index_company_docs",
        lambda saved, profile="sample": seen.update(profile=profile) or {
            "chunks": len(saved), "documents": len(saved)},
    )
    c = TestClient(app_main.app, raise_server_exceptions=False)
    headers, _ = login_headers(c)
    make_company(c, headers, name="company-X")
    r = c.post(
        "/api/pipeline/evidence-index",
        data={"company_id": "company-X"},
        files=[("files", ("a.pdf", _pdf_bytes("hi"), "application/pdf"))],
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["company_profile_id"] == "company-X"
    assert seen["profile"] == "company-X"


def test_evidence_index_forbidden_for_foreign_company(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import app.api.pipeline as pipeline_mod
    import app.main as app_main
    from helpers import isolate_auth, login_headers, make_company

    isolate_auth(monkeypatch, tmp_path)
    monkeypatch.setattr(
        pipeline_mod.retrieval, "index_company_docs",
        lambda saved, profile="sample": {"chunks": 1, "documents": 1},
    )
    c = TestClient(app_main.app, raise_server_exceptions=False)
    headers, _ = login_headers(c)
    make_company(c, headers, name="mine")
    # Authenticated but not the owner: 403, and unauthenticated: 401.
    r = c.post(
        "/api/pipeline/evidence-index",
        data={"company_id": "someone-else"},
        files=[("files", ("a.pdf", _pdf_bytes("hi"), "application/pdf"))],
        headers=headers,
    )
    assert r.status_code == 403, r.text
    r = c.post(
        "/api/pipeline/evidence-index",
        data={"company_id": "mine"},
        files=[("files", ("a.pdf", _pdf_bytes("hi"), "application/pdf"))],
    )
    assert r.status_code == 401, r.text


def _analysis_model(tid):
    from app.models.tender import TenderAnalysisResult

    return TenderAnalysisResult(
        tender_id=tid, analysis_status="SUCCESS", metadata={},
        requirements=[], issues=[], total_pages=0, processed_pages=0,
        unreadable_pages=0, requirements_needing_review=0, warnings=[],
        failed_chunks=[], linked_documents=[], completeness={},
        analysis_version="1.0",
    )


def _evidence_model(tid, profile):
    from app.models.evidence import EvidenceMap

    return EvidenceMap(
        tender_id=tid, company_profile_id=profile, items=[],
        unmapped_requirements=[], warnings=[],
    )


def _compliance_model(tid):
    from app.models.compliance import ComplianceMatrix

    return ComplianceMatrix(
        tender_id=tid, items=[], blockers=[], summary={}, decision="PROCEED",
    )


def test_resume_scopes_company(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import app.api.pipeline as pipeline_mod
    import app.main as app_main
    from helpers import isolate_auth, login_headers, make_company

    isolate_auth(monkeypatch, tmp_path)
    seen = {}
    monkeypatch.setattr(
        pipeline_mod.ComplianceService, "resume_pipeline",
        staticmethod(lambda tid, new_docs=None, company_profile_id="sample",
                     waived_blocker_ids=None, fix_map=None: (
            seen.update(profile=company_profile_id, fix_map=fix_map),
            {
                "analysis": _analysis_model(tid),
                "evidence": _evidence_model(tid, company_profile_id),
                "compliance": _compliance_model(tid),
            },
        )[1]),
    )
    c = TestClient(app_main.app, raise_server_exceptions=False)
    headers, _ = login_headers(c)
    make_company(c, headers, name="company-Z")
    r = c.post("/api/pipeline/resume",
               data={"tender_id": "t1", "company_id": "company-Z"},
               headers=headers)
    assert r.status_code == 200, r.text
    assert seen["profile"] == "company-Z"
    assert seen["fix_map"] is None  # no per-blocker files attached
    assert r.json()["evidence"]["company_profile_id"] == "company-Z"
