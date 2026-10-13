from typing import Any

from app.agents.bidprep_agent.service import BidPrepService
from app.agents.compliance_agent.service import ComplianceService
from app.agents.evidence_agent.service import EvidenceAgentService
from app.agents.tender_analyst.service import TenderAnalystService
from app.agents.verification_agent.service import VerificationService
from app.models.compliance import ComplianceMatrix
from app.models.evidence import EvidenceMap
from app.models.tender import TenderAnalysisResult
from app.orchestrator.state import OrchestratorState
from app.services.observability import observability

MAX_FIX_ATTEMPTS = 2


def _snapshot(state: OrchestratorState) -> dict[str, Any]:
    return {
        "tender_id": state["tender_id"],
        "document_name": state.get("document_name", ""),
        "analysis": state.get("analysis"),
        "evidence": state.get("evidence"),
        "compliance": state.get("compliance"),
        "package": state.get("package"),
        "verification": state.get("verification"),
        "pipeline_status": state.get("pipeline_status", "RUNNING"),
    }


def p1_node(state: OrchestratorState) -> OrchestratorState:
    observability.log_event("node_start", node="orchestrator_p1", run_id=state["run_id"])
    analysis = TenderAnalystService.analyze_tender(
        state["tender_pdf_path"],
        document_name=state.get("document_name", "tender.pdf"),
        fetch_links=state.get("fetch_links", True),
    )
    state["analysis"] = analysis.model_dump()
    state["tender_id"] = analysis.tender_id
    return state


def p2_node(state: OrchestratorState) -> OrchestratorState:
    observability.log_event("node_start", node="orchestrator_p2", run_id=state["run_id"])
    d = state["analysis"]
    analysis = TenderAnalysisResult(**(dict(d) if isinstance(d, dict) else {}))
    evidence = EvidenceAgentService.map_evidence(
        analysis, state.get("company_profile_id", "sample")
    )
    state["evidence"] = evidence.model_dump()
    return state


def p3_node(state: OrchestratorState) -> OrchestratorState:
    observability.log_event("node_start", node="orchestrator_p3", run_id=state["run_id"])
    d = state["analysis"]
    analysis = TenderAnalysisResult(**(dict(d) if isinstance(d, dict) else {}))
    e = state["evidence"]
    evidence = EvidenceMap(**(dict(e) if isinstance(e, dict) else {}))
    compliance = ComplianceService.evaluate(analysis, evidence)
    state["compliance"] = compliance.model_dump()
    return state


def _models(state: OrchestratorState):
    a = state["analysis"]
    e = state["evidence"]
    c = state["compliance"]
    return (
        TenderAnalysisResult(**(dict(a) if isinstance(a, dict) else {})),
        EvidenceMap(**(dict(e) if isinstance(e, dict) else {})),
        ComplianceMatrix(**(dict(c) if isinstance(c, dict) else {})),
    )


def p4_draft_node(state: OrchestratorState) -> OrchestratorState:
    observability.log_event(
        "node_start", node="orchestrator_p4_draft", run_id=state["run_id"],
        fix_attempt=max(0, state.get("fix_attempts", 0)),
    )
    analysis, evidence, compliance = _models(state)
    prev_package = state.get("package")
    prev_findings: list[dict[str, Any]] = []
    draft_req_ids: list[str] | None = None
    if prev_package:
        failed = [
            f for f in (state.get("verification") or {}).get("findings", [])
            if not f.get("passed", True) and f.get("severity") == "blocking"
        ]
        prev_findings = failed
        draft_req_ids = sorted({
            str(f.get("requirement_id"))
            for f in failed if f.get("requirement_id")
        }) or None
    package = BidPrepService.build_package(
        analysis, evidence, compliance,
        requirement_ids=draft_req_ids, findings_to_fix=prev_findings or None,
    )
    if prev_package and draft_req_ids is not None:
        # Fix loop: keep good drafts, swap in the fixed ones.
        kept = [
            d for d in prev_package.get("drafts", [])
            if str(d.get("requirement_id")) not in set(draft_req_ids)
        ]
        merged = kept + package.model_dump()["drafts"]
        pkg = package.model_dump()
        pkg["drafts"] = merged
        state["package"] = pkg
    else:
        state["package"] = package.model_dump()
    return state


def p4_verify_node(state: OrchestratorState) -> OrchestratorState:
    observability.log_event("node_start", node="orchestrator_p4_verify", run_id=state["run_id"])
    analysis, evidence, compliance = _models(state)
    package = state.get("package") or {}
    report = VerificationService.verify(
        tender_id=analysis.tender_id,
        requirements=[r.model_dump() for r in analysis.requirements],
        evidence_items=[i.model_dump() for i in evidence.items],
        drafts=package.get("drafts", []),
        risk_report=package.get("risk_report", []),
        blockers=[b.model_dump() for b in compliance.blockers],
        compliance_items=[i.model_dump() for i in compliance.items],
    )
    state["verification"] = report.model_dump()
    return state


def pause_node(state: OrchestratorState) -> OrchestratorState:
    state["pipeline_status"] = "PAUSED_ON_BLOCKERS"
    state["checkpoint_path"] = ComplianceService.save_checkpoint(_snapshot(state))
    observability.log_event(
        "pipeline_paused", tender_id=state.get("tender_id", "?"), run_id=state["run_id"]
    )
    return state


def finalize_node(state: OrchestratorState) -> OrchestratorState:
    state["pipeline_status"] = "COMPLETED"
    state["checkpoint_path"] = ComplianceService.save_checkpoint(_snapshot(state))
    observability.log_event(
        "pipeline_completed", tender_id=state.get("tender_id", "?"), run_id=state["run_id"]
    )
    return state
