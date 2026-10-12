"""Resume-as-background-job: fast 202, staged files, pollable result (offline)."""

import io

import pymupdf


def _pdf_bytes(text="Fix certificate"):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


def _analysis(reqs):
    return {
        "tender_id": "t-resume-job",
        "analysis_status": "SUCCESS",
        "metadata": {},
        "requirements": reqs,
        "issues": [],
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


def _req(rid):
    return {
        "requirement_id": rid, "title": "Must upload cert",
        "original_text": "Bidder must upload cert.", "category": "OTHER",
        "semantic_type": None, "mandatory": True,
        "applicability_condition": None, "threshold": None,
        "required_evidence": ["cert"], "source_references": [],
        "depends_on": [], "confidence": None, "needs_review": False,
        "review_reason": None, "normalized_description": "t",
    }


def _evidence(status):
    return {
        "tender_id": "t-resume-job",
        "items": [{
            "requirement_id": "R1", "requirement_title": "Must upload cert",
            "status": status, "confidence": 0.0 if status == "missing" else 0.9,
            "hits": [], "computed_value": None, "computed_unit": None,
            "notes": "nothing" if status == "missing" else "doc uploaded",
        }],
        "unmapped_requirements": ["R1"] if status == "missing" else [],
        "warnings": [],
    }


def _seed_checkpoint():
    from app.agents.compliance_agent.rules import (
        build_matrix,
        evaluate_compliance,
    )
    from app.agents.compliance_agent.service import ComplianceService
    from app.models.evidence import EvidenceMap
    from app.models.tender import TenderAnalysisResult

    analysis = TenderAnalysisResult(**_analysis([_req("R1")]))
    evidence = EvidenceMap(**_evidence("missing"))
    items, blockers = evaluate_compliance(
        analysis.model_dump(), evidence.model_dump()
    )
    assert len(blockers) == 1
    ComplianceService.save_checkpoint({
        "tender_id": "t-resume-job", "document_name": "d.pdf",
        "analysis": analysis.model_dump(), "evidence": evidence.model_dump(),
        "compliance": build_matrix("t-resume-job", items, blockers).model_dump(),
    })


def _isolate(monkeypatch, tmp_path):
    from app.core import config as config_mod

    monkeypatch.setattr(config_mod.settings, "CHECKPOINT_DIR", str(tmp_path / "cp"))
    monkeypatch.setattr(
        config_mod.settings, "JOBS_DB_PATH", str(tmp_path / "jobs.sqlite")
    )
    monkeypatch.setattr(
        config_mod.settings, "UPLOAD_DIR", str(tmp_path / "uploads")
    )
    monkeypatch.setattr(
        config_mod.settings, "AUTH_DB_PATH", str(tmp_path / "auth.sqlite")
    )


def _authed(client, company="company-Z"):
    from helpers import login_headers, make_company

    headers, _ = login_headers(client)
    make_company(client, headers, name=company)
    return headers


def test_resume_job_unknown_tender_404(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import app.main as app_main

    _isolate(monkeypatch, tmp_path)
    c = TestClient(app_main.app, raise_server_exceptions=False)
    headers = _authed(c, company="sample")
    r = c.post("/api/pipeline/resume-job", data={"tender_id": "nope"},
               headers=headers)
    assert r.status_code == 404, r.text


def test_translate_fix_map_handles_renames():
    import app.api.pipeline as pipeline_mod

    staged = [("a.pdf", "a.pdf"), ("b.pdf", "b_2.pdf")]
    out = pipeline_mod._translate_fix_map(
        {"BLK-1": ["b.pdf", "a.pdf", "ghost.pdf"]}, staged
    )
    assert out == {"BLK-1": ["b_2.pdf", "a.pdf"]}


def test_scoped_retrieval_searches_only_fix_docs(monkeypatch):
    """retrieve_evidence_node with source_documents constrains every query
    to the fix files (Chroma where-filter), never the whole index."""
    import app.agents.evidence_agent.nodes as nodes_mod

    calls = []

    def fake_query(query_text, company_profile_id="sample", top_k=None,
                   document_name=None):
        calls.append(document_name)
        return []

    monkeypatch.setattr(nodes_mod, "query_evidence", fake_query)
    state = {
        "tender_id": "t", "company_profile_id": "sample",
        "requirements": [{
            "requirement_id": "R1", "title": "Must upload cert",
            "normalized_description": "d", "threshold": {},
            "required_evidence": ["cert"],
        }],
        "retrieved": {}, "items": [], "unmapped": [], "warnings": [],
        "final_result": None, "source_documents": ["fix.pdf"],
    }
    out = nodes_mod.retrieve_evidence_node(state)
    assert calls, "expected scoped queries"
    assert set(calls) == {"fix.pdf"}
    assert out["retrieved"]["R1"] == []


def test_fix_map_scopes_recheck_per_blocker(monkeypatch, tmp_path):
    """With fix_map, only the blocker's own requirements are re-checked and
    only against its own fix files — other missing requirements are untouched
    and keep blocking."""
    from app.agents.compliance_agent.rules import (
        build_matrix,
        evaluate_compliance,
    )
    from app.agents.compliance_agent.service import ComplianceService
    from app.models.evidence import EvidenceMap
    from app.models.tender import TenderAnalysisResult
    from app.services import jobs as job_store

    _isolate(monkeypatch, tmp_path)

    def _req2(rid, title):
        r = _req("R1")
        r["requirement_id"] = rid
        r["title"] = title
        return r

    analysis = TenderAnalysisResult(**_analysis([_req2("R1", "Cert A"), _req2("R2", "Cert B")]))
    both_missing = {
        "tender_id": "t-resume-job",
        "items": [
            {"requirement_id": "R1", "requirement_title": "Cert A",
             "status": "missing", "confidence": 0.0, "hits": [],
             "computed_value": None, "computed_unit": None, "notes": "nothing"},
            {"requirement_id": "R2", "requirement_title": "Cert B",
             "status": "missing", "confidence": 0.0, "hits": [],
             "computed_value": None, "computed_unit": None, "notes": "nothing"},
        ],
        "unmapped_requirements": ["R1", "R2"], "warnings": [],
    }
    evidence = EvidenceMap(**both_missing)
    items, blockers = evaluate_compliance(
        analysis.model_dump(), evidence.model_dump()
    )
    assert len(blockers) == 2
    blk_r1 = next(
        b["blocker_id"] for b in blockers if b["affected_requirement_ids"] == ["R1"]
    )
    ComplianceService.save_checkpoint({
        "tender_id": "t-resume-job", "document_name": "d.pdf",
        "analysis": analysis.model_dump(), "evidence": evidence.model_dump(),
        "compliance": build_matrix("t-resume-job", items, blockers).model_dump(),
    })

    seen = {}

    def fake_map(tender_result, company_profile_id="sample", requirement_ids=None,
                 source_documents=None):
        seen["requirement_ids"] = requirement_ids
        seen["source_documents"] = source_documents
        satisfied = {
            "tender_id": "t-resume-job",
            "items": [
                {"requirement_id": rid, "requirement_title": rid,
                 "status": "satisfied", "confidence": 0.9, "hits": [],
                 "computed_value": None, "computed_unit": None, "notes": "in fix file"}
                for rid in (requirement_ids or [])
            ],
            "unmapped_requirements": [], "warnings": [],
        }
        return EvidenceMap(**satisfied)

    import app.agents.evidence_agent.service as ev_svc

    monkeypatch.setattr(ev_svc.EvidenceAgentService, "map_evidence",
                        staticmethod(fake_map))

    job_id = job_store.create_resume_job("t-resume-job", "company-Z")
    job_store.run_resume_sync(
        job_id, "t-resume-job", "company-Z", None, {blk_r1: ["fix.pdf"]}
    )
    # Only BLK-001's requirement re-checked, only against its fix file.
    assert seen["requirement_ids"] == ["R1"]
    assert seen["source_documents"] == ["fix.pdf"]

    job = job_store.get_job(job_id)
    assert job["status"] == "DONE", job
    by_id = {i["requirement_id"]: i["status"] for i in job["result"]["evidence"]["items"]}
    assert by_id == {"R1": "satisfied", "R2": "missing"}
    # R2 still blocks: scoped re-check resolved one blocker, not the run.
    assert job["result"]["pipeline_status"] == "PAUSED_ON_BLOCKERS"
    remaining = job["result"]["compliance"]["blockers"]
    assert [r for b in remaining for r in b["affected_requirement_ids"]] == ["R2"]


def test_resume_job_queues_stages_and_completes(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import app.main as app_main
    from app.services import jobs as job_store

    _isolate(monkeypatch, tmp_path)
    _seed_checkpoint()

    indexed = {}

    def fake_index(docs, company_id, replace=False):
        indexed["docs"] = list(docs)
        indexed["company"] = company_id
        return {"documents": len(docs), "chunks": len(docs)}

    import app.agents.evidence_agent.retrieval as retr_mod
    import app.agents.evidence_agent.service as ev_svc
    from app.models.evidence import EvidenceMap

    monkeypatch.setattr(retr_mod, "index_company_docs", fake_index)
    # resume_pipeline imports index_company_docs lazily from the same module
    # object, so the patch above is what the worker sees.
    monkeypatch.setattr(
        ev_svc.EvidenceAgentService, "map_evidence",
        staticmethod(lambda *a, **k: EvidenceMap(**_evidence("satisfied"))),
    )
    # Don't leak worker threads in tests: run inline on demand.
    captured = {}
    monkeypatch.setattr(
        job_store, "submit_resume_job",
        lambda *a, **k: captured.update(args=a, kwargs=k),
    )

    import json as _json

    c = TestClient(app_main.app, raise_server_exceptions=False)
    headers = _authed(c)
    r = c.post(
        "/api/pipeline/resume-job",
        data={"tender_id": "t-resume-job", "company_id": "company-Z",
              "waive": ["BLK-999"],
              "fix_map": _json.dumps({"BLK-001": ["fix.pdf"]})},
        files=[("files", ("fix.pdf", io.BytesIO(_pdf_bytes()), "application/pdf"))],
        headers=headers,
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    staged = sorted(
        f for f in __import__("os").listdir(job_store.resume_upload_dir(job_id))
    )
    assert staged == ["fix.pdf"]
    # Per-blocker mapping survived staging: BLK-001 -> its own fix file.
    assert captured["args"][4] == {"BLK-001": ["fix.pdf"]}

    job_store.run_resume_sync(
        job_id, "t-resume-job", "company-Z", ["BLK-999"], captured["args"][4]
    )
    job = job_store.get_job(job_id)
    assert job["status"] == "DONE", job
    # Fix doc was appended to the right company's index.
    assert indexed["company"] == "company-Z"
    assert [d["name"] for d in indexed["docs"]] == ["fix.pdf"]
    # Stored result is PipelineResult-shaped and unblocked.
    assert job["result"]["tender_id"] == "t-resume-job"
    assert job["result"]["pipeline_status"] == "COMPLETED"
    assert job["result"]["compliance"]["decision"] == "PROCEED"


def _seed_two_missing():
    """Checkpoint with R1+R2 both missing -> BLK-001(R1), BLK-002(R2)."""
    from app.agents.compliance_agent.rules import (
        build_matrix,
        evaluate_compliance,
    )
    from app.agents.compliance_agent.service import ComplianceService
    from app.models.evidence import EvidenceMap
    from app.models.tender import TenderAnalysisResult

    def _reqX(rid, title):
        r = _req("R1")
        r["requirement_id"] = rid
        r["title"] = title
        return r

    analysis = TenderAnalysisResult(
        **_analysis([_reqX("R1", "Cert A"), _reqX("R2", "Cert B")])
    )
    items = []
    for rid, title in (("R1", "Cert A"), ("R2", "Cert B")):
        items.append({
            "requirement_id": rid, "requirement_title": title,
            "status": "missing", "confidence": 0.0, "hits": [],
            "computed_value": None, "computed_unit": None, "notes": "nothing",
        })
    evidence = EvidenceMap(
        tender_id="t-resume-job", items=items,
        unmapped_requirements=["R1", "R2"], warnings=[],
    )
    ev_items, blockers = evaluate_compliance(
        analysis.model_dump(), evidence.model_dump()
    )
    assert [b["blocker_id"] for b in blockers] == ["BLK-001", "BLK-002"]
    ComplianceService.save_checkpoint({
        "tender_id": "t-resume-job", "document_name": "d.pdf",
        "analysis": analysis.model_dump(), "evidence": evidence.model_dump(),
        "compliance": build_matrix("t-resume-job", ev_items, blockers).model_dump(),
    })


def _satisfied_map(ids):
    from app.models.evidence import EvidenceMap

    return EvidenceMap(
        tender_id="t-resume-job",
        items=[{
            "requirement_id": rid, "requirement_title": rid,
            "status": "satisfied", "confidence": 0.9, "hits": [],
            "computed_value": None, "computed_unit": None, "notes": "in fix file",
        } for rid in ids],
        unmapped_requirements=[], warnings=[],
    )


def test_waive_survives_blocker_renumbering(monkeypatch, tmp_path):
    """The reported stuck-blocked bug: fix resolves R1, so old BLK-002(R2)
    is renumbered to BLK-001(R2) on re-evaluation. The human waive of the
    OLD id must still apply — matching is by finding, not by number."""
    import app.agents.evidence_agent.service as ev_svc
    from app.services import jobs as job_store

    _isolate(monkeypatch, tmp_path)
    _seed_two_missing()
    monkeypatch.setattr(
        ev_svc.EvidenceAgentService, "map_evidence",
        staticmethod(
            lambda tender_result, company_profile_id="sample",
            requirement_ids=None, source_documents=None:
            _satisfied_map(requirement_ids or [])
        ),
    )

    # R1 fixed by upload; R2 waived under its OLD number BLK-002.
    job_id = job_store.create_resume_job("t-resume-job", "company-Z")
    job_store.run_resume_sync(
        job_id, "t-resume-job", "company-Z", ["BLK-002"], {"BLK-001": ["fix.pdf"]}
    )
    job = job_store.get_job(job_id)
    assert job["status"] == "DONE", job
    compliance = job["result"]["compliance"]
    assert compliance["decision"] == "PROCEED", compliance["blockers"]
    assert compliance["blockers"] == []
    waived = compliance["waived_blockers"]
    assert len(waived) == 1
    assert waived[0]["affected_requirement_ids"] == ["R2"]
    assert job["result"]["pipeline_status"] == "COMPLETED"


def test_waive_only_skips_remap(monkeypatch, tmp_path):
    """Waive-only resume must not burn LLM remapping: evidence is unchanged,
    so straight to re-evaluation + waivers (also keeps IDs stable)."""
    import app.agents.evidence_agent.service as ev_svc
    from app.services import jobs as job_store

    _isolate(monkeypatch, tmp_path)
    _seed_checkpoint()

    def _boom(*a, **k):
        raise AssertionError("remap must be skipped on waive-only resume")

    monkeypatch.setattr(ev_svc.EvidenceAgentService, "map_evidence",
                        staticmethod(_boom))
    job_id = job_store.create_resume_job("t-resume-job", "company-Z")
    job_store.run_resume_sync(job_id, "t-resume-job", "company-Z", ["BLK-001"])
    job = job_store.get_job(job_id)
    assert job["status"] == "DONE", job
    assert job["result"]["compliance"]["decision"] == "PROCEED"


def test_empty_scope_blocker_never_triggers_full_remap(monkeypatch, tmp_path):
    """A blocker with no affected requirements must be skipped — never fall
    through to an unscoped remap that would nuke good evidence with misses."""
    import json as _json

    import app.agents.evidence_agent.service as ev_svc
    from app.agents.compliance_agent.service import ComplianceService
    from app.services import jobs as job_store

    _isolate(monkeypatch, tmp_path)
    _seed_checkpoint()
    path = ComplianceService.checkpoint_path("t-resume-job")
    with open(path, encoding="utf-8") as f:
        snap = _json.load(f)
    snap["compliance"]["blockers"][0]["affected_requirement_ids"] = []
    with open(path, "w", encoding="utf-8") as f:
        _json.dump(snap, f)

    def _boom(*a, **k):
        raise AssertionError("empty scope must skip mapping entirely")

    monkeypatch.setattr(ev_svc.EvidenceAgentService, "map_evidence",
                        staticmethod(_boom))
    job_id = job_store.create_resume_job("t-resume-job", "company-Z")
    job_store.run_resume_sync(
        job_id, "t-resume-job", "company-Z", None, {"BLK-001": ["fix.pdf"]}
    )
    job = job_store.get_job(job_id)
    assert job["status"] == "DONE", job
    # Still paused (nothing re-checkable), but nothing destroyed either.
    assert job["result"]["pipeline_status"] == "PAUSED_ON_BLOCKERS"
    by_id = {i["requirement_id"]: i["status"]
             for i in job["result"]["evidence"]["items"]}
    assert by_id == {"R1": "missing"}


def test_p4_runs_when_resume_unblocks(monkeypatch, tmp_path):
    """Unblocked resume finishes the pipeline: bid package + verification
    are produced and stored, not left null."""
    import app.agents.bidprep_agent.service as bp_svc
    import app.agents.evidence_agent.service as ev_svc
    import app.agents.verification_agent.service as vf_svc
    from app.models.bid import BidPackage, VerificationReport
    from app.services import jobs as job_store

    _isolate(monkeypatch, tmp_path)
    _seed_checkpoint()
    monkeypatch.setattr(
        ev_svc.EvidenceAgentService, "map_evidence",
        staticmethod(
            lambda tender_result, company_profile_id="sample",
            requirement_ids=None, source_documents=None:
            _satisfied_map(requirement_ids or [])
        ),
    )
    monkeypatch.setattr(
        bp_svc.BidPrepService, "build_package",
        staticmethod(
            lambda analysis, evidence, compliance: BidPackage(
                tender_id="t-resume-job", drafts=[], checklist=["c"],
                risk_report=[],
            )
        ),
    )
    monkeypatch.setattr(
        vf_svc.VerificationService, "verify",
        staticmethod(
            lambda **k: VerificationReport(
                tender_id="t-resume-job", passed=True, findings=[]
            )
        ),
    )
    job_id = job_store.create_resume_job("t-resume-job", "company-Z")
    job_store.run_resume_sync(
        job_id, "t-resume-job", "company-Z", None, {"BLK-001": ["fix.pdf"]}
    )
    job = job_store.get_job(job_id)
    assert job["status"] == "DONE", job
    assert job["result"]["pipeline_status"] == "COMPLETED"
    assert job["result"]["package"]["checklist"] == ["c"]
    assert job["result"]["verification"]["passed"] is True


def test_reindex_same_filename_replaces(monkeypatch, tmp_path):
    """Re-uploading the same fix filename must replace its chunks, not crash
    Chroma on duplicate IDs or double-count them."""
    from app.agents.evidence_agent import retrieval as retr_mod
    from app.core import config as config_mod

    monkeypatch.setattr(
        config_mod.settings, "EVIDENCE_CHROMA_DIR", str(tmp_path / "chroma")
    )
    doc = {"path": str(tmp_path / "fix.pdf"), "name": "fix.pdf"}
    (tmp_path / "fix.pdf").write_bytes(_pdf_bytes("Turnover certificate text"))
    first = retr_mod.index_company_docs([doc], "co", replace=False)
    second = retr_mod.index_company_docs([doc], "co", replace=False)
    assert first["documents"] == 1 and second["documents"] == 1
    inv = retr_mod.list_indexed_documents("co")
    assert [f["name"] for f in inv["files"]] == ["fix.pdf"]
    assert inv["chunks"] == first["chunks"] == second["chunks"]
