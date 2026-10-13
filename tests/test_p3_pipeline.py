"""Tests for P3 checkpoint/resume and P1 multi-document wiring (offline)."""

import pymupdf

from app.agents.compliance_agent.service import ComplianceService
from app.core import config as config_mod


def _analysis(reqs, metadata=None, issues=None, tender_id="t1"):
    return {
        "tender_id": tender_id,
        "analysis_status": "SUCCESS",
        "metadata": metadata or {},
        "requirements": reqs,
        "issues": issues or [],
        "total_pages": 1,
        "processed_pages": 1,
        "unreadable_pages": 0,
        "requirements_needing_review": 0,
        "warnings": [],
        "failed_chunks": [],
        "linked_documents": [],
        "completeness": {},
        "analysis_version": "1.0",
    }


def _evidence(items, tender_id="t1"):
    return {
        "tender_id": tender_id,
        "company_profile_id": "sample",
        "items": items,
        "unmapped_requirements": [],
        "warnings": [],
    }


def _req(rid, title="T", mandatory=True, threshold=None):
    return {
        "requirement_id": rid, "title": title, "original_text": title,
        "category": "OTHER", "semantic_type": None, "mandatory": mandatory,
        "applicability_condition": None, "threshold": threshold,
        "required_evidence": [], "source_references": [],
        "depends_on": [], "confidence": None, "needs_review": False,
        "review_reason": None, "normalized_description": title,
    }


def _ev(rid, status, notes="n"):
    return {
        "requirement_id": rid, "requirement_title": rid, "status": status,
        "confidence": 0.8, "hits": [], "computed_value": None,
        "computed_unit": None, "notes": notes,
    }


def test_checkpoint_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod.settings, "CHECKPOINT_DIR", str(tmp_path))
    payload = {"tender_id": "abc-123", "analysis": {"a": 1}}
    path = ComplianceService.save_checkpoint(payload)
    loaded = ComplianceService.load_checkpoint("abc-123")
    assert loaded["analysis"] == {"a": 1}
    assert loaded["saved_at"]
    assert path.endswith("abc-123.json")


def test_checkpoint_missing_raises(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr(config_mod.settings, "CHECKPOINT_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        ComplianceService.load_checkpoint("nope")


def test_evaluate_routes_pause_on_blocker():
    from app.models.evidence import EvidenceMap
    from app.models.tender import TenderAnalysisResult

    analysis = TenderAnalysisResult(
        **_analysis([_req("R1", "Must upload", True)])
    )
    evidence = EvidenceMap(
        **_evidence([_ev("R1", "missing", "nothing found")])
    )
    matrix = ComplianceService.evaluate(analysis, evidence)
    assert matrix.decision == "PAUSE"
    assert len(matrix.blockers) == 1
    assert matrix.summary["non_compliant"] == 1


def test_evaluate_routes_proceed_when_clean():
    from app.models.evidence import EvidenceMap
    from app.models.tender import TenderAnalysisResult

    analysis = TenderAnalysisResult(
        **_analysis([_req("R1", "Opening date", False)])
    )
    evidence = EvidenceMap(**_evidence([_ev("R1", "missing")]))
    # INFORMATIONAL default? No semantic -> mandatory False -> PENDING, no blocker.
    matrix = ComplianceService.evaluate(analysis, evidence)
    assert matrix.decision == "PROCEED"


def test_resume_reruns_only_affected(monkeypatch, tmp_path):
    """Resume appends fix docs, re-maps ONLY affected reqs, reassesses."""
    from app.models.evidence import EvidenceMap
    from app.models.tender import TenderAnalysisResult
    import app.agents.compliance_agent.service as svc_mod

    monkeypatch.setattr(config_mod.settings, "CHECKPOINT_DIR", str(tmp_path))
    reqs = [
        _req("R1", "Must upload", True),
        _req("R2", "Opening", False),
    ]
    analysis = TenderAnalysisResult(**_analysis(reqs))
    # R2 already satisfied -> only R1 is affected and re-mapped.
    evidence = EvidenceMap(**_evidence([
        _ev("R1", "missing", "nothing"), _ev("R2", "satisfied", "on file"),
    ]))
    from app.agents.compliance_agent.rules import build_matrix, evaluate_compliance

    items, blockers = evaluate_compliance(
        analysis.model_dump(), evidence.model_dump()
    )
    ComplianceService.save_checkpoint({
        "tender_id": "t1", "document_name": "d.pdf",
        "analysis": analysis.model_dump(), "evidence": evidence.model_dump(),
        "compliance": build_matrix("t1", items, blockers).model_dump(),
    })

    remapped_ids = []

    def fake_map(tender_result, company_profile_id="sample", requirement_ids=None):
        remapped_ids.extend(requirement_ids or [])
        return EvidenceMap(**_evidence([
            _ev("R1", "satisfied", "doc uploaded"),
        ]))

    # Patch where resume looks it up: EvidenceAgentService.map_evidence.
    import app.agents.evidence_agent.service as ev_svc

    monkeypatch.setattr(ev_svc.EvidenceAgentService, "map_evidence",
                        staticmethod(fake_map))
    out = ComplianceService.resume_pipeline("t1", new_docs=None)
    # Only the affected requirement (R1 missing) was re-mapped; R2 untouched.
    assert remapped_ids == ["R1"]
    assert out["compliance"].decision == "PROCEED"
    reloaded = ComplianceService.load_checkpoint("t1")
    assert reloaded["compliance"]["decision"] == "PROCEED"


def _pdf_bytes(text):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


def test_analyze_tender_multi_doc_attribution(monkeypatch, tmp_path):
    """Full P1 offline (stubbed LLM): annex pages keep their document_name,
    requirements attribute correctly, fetched annex suppresses its issue."""
    from app.agents.tender_analyst import nodes as N
    from app.agents.tender_analyst.llm_schemas import LLMMetadata, LLMSimpleRequirementList
    from app.agents.tender_analyst.service import TenderAnalystService

    annex = tmp_path / "atc.pdf"
    annex.write_bytes(_pdf_bytes("ATC special condition: EMD exempt for MSE."))

    main = pymupdf.open()
    page = main.new_page()
    page.insert_text((72, 72), "Submission deadline 30 November 2026. See ATC document attached.")
    main_path = tmp_path / "main.pdf"
    main.save(str(main_path))

    # Fake the crawl: annex "fetched" from the main file without network.
    import app.agents.tender_analyst.service as svc_mod

    def fake_crawl(file_path, document_name, tender_id, pages, warnings):
        from app.tools.document_parser import DocumentParser

        res = DocumentParser.parse_pdf(str(annex), document_name="atc.pdf")
        for p in res.pages:
            pages.append(p.model_dump())
        return [{"url": "embedded:atc.pdf", "depth": 1, "via": "embedded-attachment",
                 "status": "fetched", "reason": "test", "filename": "atc.pdf", "pages": 1}]

    monkeypatch.setattr(svc_mod.TenderAnalystService, "_fetch_linked_documents",
                        staticmethod(fake_crawl))

    def fake_invoke(schema, prompt, operation="", fallback_schemas=()):
        if schema is LLMMetadata:
            return LLMMetadata(tender_title="Test tender")
        # One requirement per chunk, echoing the owning document. The main
        # chunk references the ATC so suppression is genuinely exercised.
        # Wording is realistic: the exemption keeps its relief marker and the
        # deadline its obligation wording, so the proof-free-detail filter
        # (INFORMATIONAL with no evidence) keeps both, as production would.
        if "atc.pdf" in prompt:
            text = "ATC special condition: EMD exempt for MSE bidders"
        else:
            text = "Bidder must submit the bid before 30 November 2026. See ATC document attached."
        return LLMSimpleRequirementList(requirements=[{
            "requirement_id": "R1", "category": "OTHER", "title": text,
            "original_text": text, "mandatory": True, "page": 1, "section": "S",
        }])

    monkeypatch.setattr(N, "_invoke_structured", fake_invoke)
    result = TenderAnalystService.analyze_tender(str(main_path), "main.pdf")

    docs = {r.source_references[0].document_name for r in result.requirements}
    assert docs == {"main.pdf", "atc.pdf"}
    assert [m["filename"] for m in result.linked_documents] == ["atc.pdf"]
    # The fetched ATC must NOT raise a missing-attachment issue.
    assert not any(
        "ATC document" in i.description and i.issue_type == "MISSING_ATTACHMENT"
        for i in result.issues
    )
    assert len({r.requirement_id for r in result.requirements}) == len(result.requirements)


def test_resume_waive_moves_blocker_to_waived(tmp_path, monkeypatch):
    """Waived blockers leave the blocking set (auditable) and unpause."""
    from app.agents.compliance_agent.service import ComplianceService
    from app.core import config as config_mod
    from app.models.evidence import EvidenceMap
    from app.models.tender import TenderAnalysisResult

    monkeypatch.setattr(config_mod.settings, "CHECKPOINT_DIR", str(tmp_path))
    reqs = [
        {"requirement_id": "R1", "title": "Must upload", "original_text": "t",
         "category": "OTHER", "semantic_type": None, "mandatory": True,
         "applicability_condition": None, "threshold": None,
         "required_evidence": [], "source_references": [], "depends_on": [],
         "confidence": None, "needs_review": False, "review_reason": None,
         "normalized_description": "t"},
    ]
    analysis = TenderAnalysisResult(
        tender_id="tw", analysis_status="SUCCESS", metadata={}, requirements=reqs,
        issues=[], total_pages=1, processed_pages=1, unreadable_pages=0,
        requirements_needing_review=0, warnings=[], failed_chunks=[],
        linked_documents=[], completeness={}, analysis_version="1.0",
    )
    evidence = EvidenceMap(
        tender_id="tw",
        items=[{"requirement_id": "R1", "requirement_title": "Must upload",
                "status": "missing", "confidence": 0.0, "hits": [],
                "computed_value": None, "computed_unit": None,
                "notes": "nothing found"}],
        unmapped_requirements=["R1"], warnings=[],
    )
    from app.agents.compliance_agent.rules import build_matrix, evaluate_compliance

    items, blockers = evaluate_compliance(
        analysis.model_dump(), evidence.model_dump()
    )
    assert len(blockers) == 1
    ComplianceService.save_checkpoint({
        "tender_id": "tw", "document_name": "d.pdf",
        "analysis": analysis.model_dump(), "evidence": evidence.model_dump(),
        "compliance": build_matrix("tw", items, blockers).model_dump(),
    })

    import app.agents.evidence_agent.service as ev_svc

    monkeypatch.setattr(
        ev_svc.EvidenceAgentService, "map_evidence",
        staticmethod(lambda *a, **k: evidence),
    )
    out = ComplianceService.resume_pipeline(
        "tw", new_docs=None, waived_blocker_ids=["BLK-001"],
    )
    assert out["compliance"].decision == "PROCEED"
    assert out["compliance"].blockers == []
    assert [b.blocker_id for b in out["compliance"].waived_blockers] == ["BLK-001"]
    assert out["compliance"].summary["waived"] == 1


def test_events_stream_endpoint(monkeypatch, tmp_path):
    """SSE endpoint replays buffered agent events."""
    from fastapi.testclient import TestClient
    import app.main as app_main
    from app.services.observability import observability
    from helpers import isolate_auth, login_headers

    isolate_auth(monkeypatch, tmp_path)
    observability.log_event("node_start", node="p1", tender_id="t-stream")
    c = TestClient(app_main.app, raise_server_exceptions=False)
    headers, body = login_headers(c)
    token = body["token"]
    with c.stream(
        "GET", f"/api/events/stream?tender_id=t-stream&access_token={token}"
    ) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        first = next(r.iter_text())
    assert "node_start" in first
    assert "t-stream" in first
