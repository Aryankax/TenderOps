"""Exhaustive P1+P2 edge-case sweep (LLM mocked; parsing/retrieval real).

Every test answers: does this input crash, leak, or return garbage?
Live golden-path run is scripts-adjacent (/tmp/e2e_live.py) to spare API budget.
"""

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.agents.evidence_agent import nodes as evidence_nodes
from app.agents.tender_analyst import nodes as analyst_nodes
from app.core.config import settings
from app.main import app
from app.models.evidence import EvidenceMap
from app.models.tender import TenderAnalysisResult

client = TestClient(app)

_VALID_STATUSES = {"satisfied", "partial", "missing", "needs_review"}


def _make_pdf(path: str, text: str = "") -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    if text:
        page.insert_textbox(pymupdf.Rect(56, 56, 540, 780), text, fontsize=11)
    doc.save(path)
    doc.close()


def _analysis_result(n_reqs: int = 2):
    reqs = [
        {
            "requirement_id": f"R-{i + 1}",
            "category": "FINANCIAL_ELIGIBILITY",
            "title": f"Turnover requirement {i + 1}",
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
        for i in range(n_reqs)
    ]
    return TenderAnalysisResult(
        tender_id="t-1",
        analysis_status="SUCCESS",
        metadata={},
        requirements=reqs,
        issues=[],
        total_pages=1,
        processed_pages=1,
        unreadable_pages=0,
        requirements_needing_review=0,
        warnings=[],
        failed_chunks=[],
        completeness={},
    )


# --- P2 robustness ---------------------------------------------------------


def test_p2_empty_requirements_never_touches_llm(monkeypatch):
    called = []
    monkeypatch.setattr(
        evidence_nodes,
        "_invoke_structured",
        lambda *a, **k: called.append(1) or (_ for _ in ()).throw(AssertionError()),
    )
    res = _analysis_result(n_reqs=0)
    from app.agents.evidence_agent.service import EvidenceAgentService

    out = EvidenceAgentService.map_evidence(res)
    assert isinstance(out, EvidenceMap)
    assert out.items == [] and called == []


def test_p2_llm_total_outage_degrades_gracefully(monkeypatch):
    """Numeric reqs resolve deterministically (no LLM needed); the rest go
    needs_review with a warning instead of crashing."""
    monkeypatch.setattr(
        evidence_nodes,
        "query_evidence",
        lambda q, profile="sample", top_k=None, **kw: [
            {
                "document_name": "d.pdf",
                "page_number": 1,
                "excerpt": "turnover 12 crore",
                "score": 0.7,
                "supports": "supports",
            }
        ],
    )

    def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(evidence_nodes, "_invoke_structured", boom)
    from app.agents.evidence_agent.service import EvidenceAgentService

    res = _analysis_result()
    # R-2 carries no numeric threshold, so only the LLM could judge it.
    res.requirements[1].threshold = None
    out = EvidenceAgentService.map_evidence(res)
    assert [i.status for i in out.items] == ["satisfied", "needs_review"]
    assert out.warnings, "outage must be reported, not swallowed"


def test_p2_retrieval_crash_marks_missing(monkeypatch):
    def crash(*a, **k):
        raise RuntimeError("chroma down")

    monkeypatch.setattr(evidence_nodes, "query_evidence", crash)
    from app.agents.evidence_agent.service import EvidenceAgentService

    out = EvidenceAgentService.map_evidence(_analysis_result())
    assert all(i.status == "missing" for i in out.items)
    assert any("Retrieval failed" in w for w in out.warnings)


def test_p2_invariants_hold():
    from unittest.mock import MagicMock

    from app.agents.evidence_agent.service import EvidenceAgentService

    complaints = []

    def fake_query(q, profile="sample", top_k=None, **kw):
        return [
            {
                "document_name": "d.pdf",
                "page_number": 3,
                "excerpt": "turnover twelve crore",
                "score": 0.66,
                "supports": "supports",
            }
        ]

    def fake_llm(schema, prompt, operation, fallback_schemas=()):
        m = MagicMock()
        m.mappings = [
            {
                "requirement_id": "R-1",
                "status": "satisfied",
                "confidence": 0.92,
                "supporting_doc": "d.pdf",
                "supporting_page": 3,
                "notes": "ok",
            },
            {
                "requirement_id": "R-2",
                "status": "bogus-status",
                "confidence": 99,
            },
        ]
        return m

    orig_query = evidence_nodes.query_evidence
    orig_invoke = evidence_nodes._invoke_structured
    evidence_nodes.query_evidence = fake_query
    evidence_nodes._invoke_structured = fake_llm
    try:
        out = EvidenceAgentService.map_evidence(_analysis_result())
    finally:
        evidence_nodes.query_evidence = orig_query
        evidence_nodes._invoke_structured = orig_invoke

    by_id = {i.requirement_id: i for i in out.items}
    assert by_id["R-1"].status == "satisfied"
    # Invalid status + out-of-range confidence are coerced, never crash.
    assert by_id["R-2"].status in _VALID_STATUSES
    for item in out.items:
        assert 0.0 <= item.confidence <= 1.0
        for h in item.hits:
            assert h.document_name and h.page_number >= 1 and h.excerpt
    assert [i.requirement_id for i in out.items] == ["R-1", "R-2"]
    assert complaints == []


# --- P1 robustness ---------------------------------------------------------


def test_p1_blank_pdf_fails_cleanly(tmp_path, monkeypatch):
    p = str(tmp_path / "blank.pdf")
    _make_pdf(p)
    monkeypatch.setattr(
        analyst_nodes,
        "_invoke_structured",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no content")),
    )
    from app.agents.tender_analyst.service import TenderAnalystService

    out = TenderAnalystService.analyze_tender(p, document_name="blank.pdf")
    assert out.analysis_status in ("FAILED", "PARTIAL")
    assert out.total_pages == 1 and out.unreadable_pages == 1


def test_p1_unparseable_pdf_raises_value_error(tmp_path):
    p = str(tmp_path / "junk.pdf")
    with open(p, "wb") as f:
        f.write(b"this is not a pdf at all")
    from app.agents.tender_analyst.service import TenderAnalystService

    with pytest.raises(ValueError):
        TenderAnalystService.analyze_tender(p, document_name="junk.pdf")


def test_p1_hindi_control_chars_sanitized(tmp_path):
    p = str(tmp_path / "hindi.pdf")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(
        pymupdf.Rect(56, 56, 540, 780),
        "निविदा \x01\x13 mixed GeM tender text \x7f with controls",
        fontsize=11,
    )
    doc.save(p)
    doc.close()
    from app.tools.document_parser import DocumentParser

    res = DocumentParser.parse_pdf(p)
    assert res.pages[0].extraction_status == "SUCCESS"
    assert "\x01" not in res.pages[0].text and "\x7f" not in res.pages[0].text


# --- HTTP layer (authed; orchestrator mocked to spare LLM budget) --------


def _authed(monkeypatch, tmp_path, company="Acme"):
    from helpers import isolate_auth, login_headers, make_company

    isolate_auth(monkeypatch, tmp_path)
    headers, _ = login_headers(client)
    created = make_company(client, headers, name=company)
    return headers, created["slug"]


def test_http_garbage_pdf_returns_400_not_500(monkeypatch, tmp_path):
    from unittest.mock import patch

    headers, slug = _authed(monkeypatch, tmp_path)
    with patch(
        "app.orchestrator.service.OrchestratorService.run_tender",
        side_effect=ValueError("Failed to open PDF"),
    ):
        resp = client.post(
            "/api/pipeline/run",
            params={"company_id": slug},
            files={"file": ("junk.pdf", b"not a pdf", "application/pdf")},
            headers=headers,
        )
    assert resp.status_code == 400


def test_http_non_pdf_rejected(monkeypatch, tmp_path):
    headers, slug = _authed(monkeypatch, tmp_path)
    resp = client.post(
        "/api/pipeline/run",
        params={"company_id": slug},
        files={"file": ("tender.txt", b"hello", "text/plain")},
        headers=headers,
    )
    assert resp.status_code == 400


def test_http_missing_filename_rejected(monkeypatch, tmp_path):
    headers, slug = _authed(monkeypatch, tmp_path)
    resp = client.post(
        "/api/pipeline/run",
        params={"company_id": slug},
        files={"file": (None, b"%PDF-1.4 fake", "application/pdf")},
        headers=headers,
    )
    # 422: framework rejects nameless parts before our code runs. Either way,
    # a missing filename never reaches the pipeline.
    assert resp.status_code == 422


def test_http_oversize_rejected(monkeypatch, tmp_path):
    headers, slug = _authed(monkeypatch, tmp_path)
    big = b"%PDF-1.4\n" + b"0" * (settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024 + 1)
    resp = client.post(
        "/api/pipeline/run",
        params={"company_id": slug},
        files={"file": ("big.pdf", big, "application/pdf")},
        headers=headers,
    )
    assert resp.status_code == 400


def test_http_index_rebuild_roundtrip(tmp_path, monkeypatch):
    d = str(tmp_path / "chroma")
    monkeypatch.setattr(settings, "EVIDENCE_CHROMA_DIR", d)
    monkeypatch.setattr(settings, "EVIDENCE_MIN_SCORE", 0.0)
    from app.agents.evidence_agent import retrieval as R

    f1 = str(tmp_path / "fin.pdf")
    f2 = str(tmp_path / "iso.pdf")
    _make_pdf(f1, "Audited annual turnover 12.10 crore INR balance sheet.")
    _make_pdf(f2, "ISO 9001 certificate valid until March 2027.")
    R.index_company_docs(
        [{"path": f1, "name": "fin.pdf"}, {"path": f2, "name": "iso.pdf"}]
    )
    hits = R.query_evidence("annual turnover balance sheet")
    assert hits and hits[0]["document_name"] == "fin.pdf"
    R.reset_index()


def test_http_index_endpoint_roundtrip(monkeypatch, tmp_path):
    d = str(tmp_path / "chroma")
    monkeypatch.setattr(settings, "EVIDENCE_CHROMA_DIR", d)
    headers, slug = _authed(monkeypatch, tmp_path)
    f1 = str(tmp_path / "fin.pdf")
    _make_pdf(f1, "Audited annual turnover 12.10 crore INR balance sheet.")
    with open(f1, "rb") as fh:
        resp = client.post(
            "/api/pipeline/evidence-index",
            data={"company_id": slug},
            files=[("files", ("fin.pdf", fh, "application/pdf"))],
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["company_profile_id"] == slug
    assert body["indexed_files"] == ["fin.pdf"]


def test_http_index_rejects_non_pdf(monkeypatch, tmp_path):
    headers, slug = _authed(monkeypatch, tmp_path)
    resp = client.post(
        "/api/pipeline/evidence-index",
        data={"company_id": slug},
        files=[("files", ("co.txt", b"hello", "text/plain"))],
        headers=headers,
    )
    assert resp.status_code == 400
