"""Tests for P4 verification rules, orchestrator routing, and async jobs."""

from app.agents.verification_agent.rules import verify_package


def _req(rid, mandatory=True):
    return {"requirement_id": rid, "title": f"T-{rid}", "mandatory": mandatory,
            "threshold": None}


def _draft(rid, refs=None, claimed=None, unit=None, did=None):
    return {
        "draft_id": did or f"D-{rid}", "requirement_id": rid,
        "kind": "requirement_response", "title": f"T-{rid}", "body": "body",
        "evidence_refs": refs or [], "claimed_value": claimed,
        "claimed_unit": unit, "needs_human_signoff": False,
    }


def _hit(doc="D.pdf", page=1):
    return {"document_name": doc, "page_number": page, "excerpt": "x",
            "score": 0.8, "supports": "supports"}


def _comp(rid, status):
    return {"requirement_id": rid, "status": status}


def test_verifier_passes_clean_package():
    rep = verify_package(
        [_req("R1"), _req("R2", mandatory=False)],
        [{"requirement_id": "R1", "hits": [_hit()]},
         {"requirement_id": "R2", "hits": []}],
        [_draft("R1", refs=[{"document_name": "D.pdf", "page_number": 1}])],
        ["no blockers"],
        blockers=[],
        compliance_items=[_comp("R1", "COMPLIANT"), _comp("R2", "PENDING")],
    )
    assert rep.passed is True


def test_verifier_missing_draft_blocks():
    rep = verify_package(
        [_req("R1")], [{"requirement_id": "R1", "hits": [_hit()]}],
        [], ["risk R1 covered"], [],
        [_comp("R1", "COMPLIANT")],
    )
    assert rep.passed is False
    assert any(f.check == "draft-coverage" and f.severity == "blocking"
               for f in rep.findings)


def test_verifier_bad_citation_blocks():
    rep = verify_package(
        [_req("R1")], [{"requirement_id": "R1", "hits": [_hit("D.pdf", 1)]}],
        [_draft("R1", refs=[{"document_name": "Ghost.pdf", "page_number": 9}])],
        ["risk R1 covered"], [],
        [_comp("R1", "COMPLIANT")],
    )
    assert rep.passed is False
    assert any(f.check == "citation-resolves" for f in rep.findings)


def test_verifier_draft_for_failure_blocks():
    rep = verify_package(
        [_req("R1")], [{"requirement_id": "R1", "hits": []}],
        [_draft("R1", refs=[{"document_name": "D.pdf", "page_number": 1}])],
        ["risk R1 covered"], [],
        [_comp("R1", "NON_COMPLIANT")],
    )
    assert rep.passed is False
    assert any(f.check == "no-draft-for-failure" for f in rep.findings)


def test_verifier_numeric_mismatch_blocks():
    req = _req("R1")
    req["threshold"] = {"operator": ">=", "value": 100.0, "unit": "INR",
                        "original_value": ">= 100 INR"}
    rep = verify_package(
        [req], [{"requirement_id": "R1", "hits": [_hit()]}],
        [_draft("R1", refs=[{"document_name": "D.pdf", "page_number": 1}],
                claimed=50.0, unit="INR")],
        ["risk R1 covered"], [],
        [_comp("R1", "COMPLIANT")],
    )
    assert rep.passed is False
    assert any(f.check == "numeric-claim" for f in rep.findings)


def test_verifier_risk_gap_blocks_high_only():
    rep = verify_package(
        [_req("R1")], [{"requirement_id": "R1", "hits": [_hit()]}],
        [_draft("R1", refs=[{"document_name": "D.pdf", "page_number": 1}])],
        ["unrelated risk text"],
        [{"blocker_id": "BLK-001", "blocker_type": "MISSING_DOCUMENT",
          "description": "Missing doc for R1", "severity": "high",
          "affected_requirement_ids": ["R1"], "resolution_hint": "upload",
          "requires_human_review": True}],
        [_comp("R1", "COMPLIANT")],
    )
    assert rep.passed is False
    assert any(f.check == "risk-coverage" for f in rep.findings)


def test_orchestrator_pauses_on_blockers(monkeypatch, tmp_path):
    """Stubbed P1+P2, P3 says PAUSE -> no P4 calls, checkpoint saved."""
    from app.core import config as config_mod
    monkeypatch.setattr(config_mod.settings, "CHECKPOINT_DIR", str(tmp_path))
    import app.orchestrator.nodes as N
    from app.models.compliance import ComplianceMatrix
    from app.models.evidence import EvidenceMap
    from app.models.tender import TenderAnalysisResult

    analysis = TenderAnalysisResult(
        tender_id="t1", analysis_status="SUCCESS", metadata={},
        requirements=[], issues=[], total_pages=1, processed_pages=1,
        unreadable_pages=0, requirements_needing_review=0, warnings=[],
        failed_chunks=[], linked_documents=[], completeness={},
        analysis_version="1.0",
    )
    evidence = EvidenceMap(tender_id="t1", items=[], unmapped_requirements=[],
                           warnings=[])
    paused = ComplianceMatrix(tender_id="t1", items=[], blockers=[{
        "blocker_id": "BLK-001", "blocker_type": "MISSING_DOCUMENT",
        "description": "need doc", "severity": "high",
        "affected_requirement_ids": [], "resolution_hint": "upload",
        "requires_human_review": True,
    }], summary={}, decision="PAUSE")

    monkeypatch.setattr(
        "app.orchestrator.nodes.TenderAnalystService.analyze_tender",
        staticmethod(lambda *a, **k: analysis),
    )
    monkeypatch.setattr(
        "app.orchestrator.nodes.EvidenceAgentService.map_evidence",
        staticmethod(lambda *a, **k: evidence),
    )
    monkeypatch.setattr(
        "app.orchestrator.nodes.ComplianceService.evaluate",
        staticmethod(lambda *a, **k: paused),
    )
    called = []
    monkeypatch.setattr(
        "app.orchestrator.nodes.BidPrepService.build_package",
        staticmethod(lambda *a, **k: called.append(1)),
    )
    from app.orchestrator.service import OrchestratorService

    out = OrchestratorService.run_tender("/tmp/x.pdf", "x.pdf")
    assert out["pipeline_status"] == "PAUSED_ON_BLOCKERS"
    assert called == []  # P4 never ran
    assert out["checkpoint_path"] and out["checkpoint_path"].endswith(".json")
    assert out["compliance"]["decision"] == "PAUSE"


def test_orchestrator_fix_loop_repairs_then_completes(monkeypatch, tmp_path):
    from app.core import config as config_mod
    monkeypatch.setattr(config_mod.settings, "CHECKPOINT_DIR", str(tmp_path))
    import app.orchestrator.nodes as N
    from app.models.bid import BidPackage, VerificationReport
    from app.models.compliance import ComplianceMatrix
    from app.models.evidence import EvidenceMap
    from app.models.tender import TenderAnalysisResult

    analysis = TenderAnalysisResult(
        tender_id="t2", analysis_status="SUCCESS", metadata={},
        requirements=[], issues=[], total_pages=1, processed_pages=1,
        unreadable_pages=0, requirements_needing_review=0, warnings=[],
        failed_chunks=[], linked_documents=[], completeness={},
        analysis_version="1.0",
    )
    evidence = EvidenceMap(tender_id="t2", items=[], unmapped_requirements=[],
                           warnings=[])
    proceeded = ComplianceMatrix(tender_id="t2", items=[], blockers=[],
                                 summary={}, decision="PROCEED")
    fail_report = VerificationReport(tender_id="t2", passed=False, findings=[{
        "check": "citation-resolves", "requirement_id": "R1", "draft_id": "D-R1",
        "passed": False, "severity": "blocking", "detail": "bad cite",
    }])
    pass_report = VerificationReport(tender_id="t2", passed=True, findings=[])
    pkg = BidPackage(tender_id="t2", drafts=[], checklist=[], risk_report=[])

    monkeypatch.setattr(
        "app.orchestrator.nodes.TenderAnalystService.analyze_tender",
        staticmethod(lambda *a, **k: analysis),
    )
    monkeypatch.setattr(
        "app.orchestrator.nodes.EvidenceAgentService.map_evidence",
        staticmethod(lambda *a, **k: evidence),
    )
    monkeypatch.setattr(
        "app.orchestrator.nodes.ComplianceService.evaluate",
        staticmethod(lambda *a, **k: proceeded),
    )
    drafts_calls = []

    def fake_build(*a, **k):
        drafts_calls.append(k)
        return pkg

    monkeypatch.setattr(
        "app.orchestrator.nodes.BidPrepService.build_package",
        staticmethod(fake_build),
    )
    verifies = {"n": 0}

    def fake_verify(**kwargs):
        verifies["n"] += 1
        return fail_report if verifies["n"] == 1 else pass_report

    monkeypatch.setattr(
        "app.orchestrator.nodes.VerificationService.verify",
        staticmethod(fake_verify),
    )
    from app.orchestrator.service import OrchestratorService

    out = OrchestratorService.run_tender("/tmp/x.pdf", "x.pdf")
    assert out["pipeline_status"] == "COMPLETED"
    assert out["verification"]["passed"] is True
    assert len(drafts_calls) == 2  # initial + one fix round
    assert out["checkpoint_path"]


def test_orchestrator_fix_loop_capped(monkeypatch, tmp_path):
    from app.core import config as config_mod
    monkeypatch.setattr(config_mod.settings, "CHECKPOINT_DIR", str(tmp_path))
    from app.models.bid import BidPackage, VerificationReport
    from app.models.compliance import ComplianceMatrix
    from app.models.evidence import EvidenceMap
    from app.models.tender import TenderAnalysisResult

    analysis = TenderAnalysisResult(
        tender_id="t3", analysis_status="SUCCESS", metadata={},
        requirements=[], issues=[], total_pages=1, processed_pages=1,
        unreadable_pages=0, requirements_needing_review=0, warnings=[],
        failed_chunks=[], linked_documents=[], completeness={},
        analysis_version="1.0",
    )
    evidence = EvidenceMap(tender_id="t3", items=[], unmapped_requirements=[],
                           warnings=[])
    proceeded = ComplianceMatrix(tender_id="t3", items=[], blockers=[],
                                 summary={}, decision="PROCEED")
    fail_report = VerificationReport(tender_id="t3", passed=False, findings=[{
        "check": "x", "requirement_id": "R1", "draft_id": "D-R1",
        "passed": False, "severity": "blocking", "detail": "always bad",
    }])
    pkg = BidPackage(tender_id="t3", drafts=[], checklist=[], risk_report=[])
    calls = {"draft": 0}

    monkeypatch.setattr(
        "app.orchestrator.nodes.TenderAnalystService.analyze_tender",
        staticmethod(lambda *a, **k: analysis),
    )
    monkeypatch.setattr(
        "app.orchestrator.nodes.EvidenceAgentService.map_evidence",
        staticmethod(lambda *a, **k: evidence),
    )
    monkeypatch.setattr(
        "app.orchestrator.nodes.ComplianceService.evaluate",
        staticmethod(lambda *a, **k: proceeded),
    )

    def fake_build(*a, **k):
        calls["draft"] += 1
        return pkg

    monkeypatch.setattr(
        "app.orchestrator.nodes.BidPrepService.build_package",
        staticmethod(fake_build),
    )
    monkeypatch.setattr(
        "app.orchestrator.nodes.VerificationService.verify",
        staticmethod(lambda **k: fail_report),
    )
    from app.orchestrator.service import OrchestratorService

    out = OrchestratorService.run_tender("/tmp/x.pdf", "x.pdf")
    assert calls["draft"] == 3  # initial + 2 bounded fix rounds
    assert out["pipeline_status"] == "COMPLETED"  # ships with risks flagged
    assert out["verification"]["passed"] is False


def test_jobs_lifecycle(monkeypatch, tmp_path):
    from app.core import config as config_mod
    monkeypatch.setattr(config_mod.settings, "JOBS_DB_PATH", str(tmp_path / "j.sqlite"))
    monkeypatch.setattr(config_mod.settings, "UPLOAD_DIR", str(tmp_path / "up"))
    from app.services import jobs as J

    jid = J.create_job("d.pdf")
    assert J.get_job(jid)["status"] == "QUEUED"

    import app.services.jobs as JM  # noqa: F401 (module import for clarity)

    # Stub the orchestrator at its import site inside run_job_sync.
    import app.orchestrator.service as OS
    monkeypatch.setattr(
        OS.OrchestratorService, "run_tender",
        staticmethod(lambda *a, **k: {"tender_id": "t9", "ok": True}),
    )
    open(J.upload_path(jid), "wb").write(b"%PDF-1.4 fake")
    J.run_job_sync(jid, "d.pdf")
    job = J.get_job(jid)
    assert job["status"] == "DONE"
    assert job["result"]["tender_id"] == "t9"
    assert not __import__("os").path.exists(J.upload_path(jid))  # upload cleaned
    assert J.get_job("nope") is None
    assert J.list_jobs()[0]["id"] == jid

    jid2 = J.create_job("e.pdf")
    open(J.upload_path(jid2), "wb").write(b"%PDF-1.4 fake")

    def boom(*a, **k):
        raise RuntimeError("kaput")

    monkeypatch.setattr(OS.OrchestratorService, "run_tender", staticmethod(boom))
    J.run_job_sync(jid2, "e.pdf")
    assert J.get_job(jid2)["status"] == "FAILED"
    assert "kaput" in J.get_job(jid2)["error"]
