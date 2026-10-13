"""P1..P4 wiring through /api/pipeline/run (orchestrator mocked)."""

from unittest.mock import patch

from fastapi.testclient import TestClient
from helpers import isolate_auth, login_headers, make_company

from app.main import app
from app.models.evidence import EvidenceMap
from app.models.tender import TenderAnalysisResult

client = TestClient(app)


def _pdf_bytes(text="hello company"):
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


def _authed(monkeypatch, tmp_path, company="Acme"):
    isolate_auth(monkeypatch, tmp_path)
    headers, _ = login_headers(client)
    created = make_company(client, headers, name=company)
    return headers, created["slug"]


def _analysis():
    return TenderAnalysisResult(
        tender_id="t-1",
        analysis_status="SUCCESS",
        metadata={},
        requirements=[
            {
                "requirement_id": "R-1",
                "category": "FINANCIAL_ELIGIBILITY",
                "title": "Minimum turnover",
                "original_text": "Minimum average annual turnover 1156.37 lakh",
                "threshold": {
                    "operator": ">=",
                    "value": 115637000.0,
                    "unit": "INR",
                    "original_value": "1156.37 lakh",
                },
                "source_references": [
                    {
                        "document_name": "t.pdf",
                        "page_number": 1,
                        "source_excerpt": "turnover clause",
                    }
                ],
            }
        ],
        issues=[],
        total_pages=1,
        processed_pages=1,
        unreadable_pages=0,
        requirements_needing_review=0,
        warnings=[],
        failed_chunks=[],
        completeness={},
    )


def _evidence():
    return EvidenceMap(
        tender_id="t-1",
        company_profile_id="Acme",
        items=[
            {
                "requirement_id": "R-1",
                "requirement_title": "Minimum turnover",
                "status": "partial",
                "confidence": 0.8,
                "hits": [
                    {
                        "document_name": "financials.pdf",
                        "page_number": 1,
                        "excerpt": "turnover 12.1 crore",
                        "score": 0.7,
                    }
                ],
                "notes": "Figures quoted; sufficiency deferred to compliance.",
            }
        ],
        unmapped_requirements=[],
        warnings=[],
    )


def test_pipeline_run_wires_orchestrator(monkeypatch, tmp_path):
    headers, slug = _authed(monkeypatch, tmp_path)
    out = {"tender_id": "t-1", "analysis": _analysis(), "evidence": _evidence()}
    with patch(
        "app.orchestrator.service.OrchestratorService.run_tender",
        return_value=out,
    ) as run:
        resp = client.post(
            "/api/pipeline/run",
            params={"company_id": slug},
            files={"file": ("tender.pdf", b"%PDF-1.4 fake", "application/pdf")},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tender_id"] == "t-1"
    assert body["analysis"]["requirements"][0]["requirement_id"] == "R-1"
    assert body["evidence"]["items"][0]["status"] == "partial"
    assert run.call_args[1]["company_profile_id"] == slug


def test_pipeline_run_rejects_non_pdf(monkeypatch, tmp_path):
    headers, slug = _authed(monkeypatch, tmp_path)
    resp = client.post(
        "/api/pipeline/run",
        params={"company_id": slug},
        files={"file": ("tender.txt", b"hello", "text/plain")},
        headers=headers,
    )
    assert resp.status_code == 400


def test_pipeline_run_requires_auth():
    resp = client.post(
        "/api/pipeline/run",
        files={"file": ("tender.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 401


def test_rebuild_evidence_index(monkeypatch, tmp_path):
    headers, slug = _authed(monkeypatch, tmp_path)
    with patch(
        "app.agents.evidence_agent.retrieval.index_company_docs",
        return_value={"chunks": 2, "documents": 2},
    ) as idx:
        resp = client.post(
            "/api/pipeline/evidence-index",
            data={"company_id": slug},
            files=[("files", ("co.pdf", _pdf_bytes(), "application/pdf"))],
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["company_profile_id"] == slug
    assert body["indexed_files"] == ["co.pdf"]
    assert idx.called
