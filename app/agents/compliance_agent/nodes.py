from app.agents.compliance_agent.rules import build_matrix, evaluate_compliance
from app.agents.compliance_agent.state import ComplianceAgentState
from app.services.observability import observability


def compliance_check_node(state: ComplianceAgentState) -> ComplianceAgentState:
    """Deterministic rules over P1 requirements + P2 evidence (no LLM)."""
    tender_id = state.get("tender_id", "unknown")
    observability.log_event("node_start", node="compliance_check", tender_id=tender_id)
    try:
        items, blockers = evaluate_compliance(
            state.get("analysis", {}), state.get("evidence_map", {})
        )
        matrix = build_matrix(tender_id, items, blockers)
        state["matrix"] = matrix.model_dump()
        state["decision"] = matrix.decision
        observability.log_event(
            "node_success",
            node="compliance_check",
            tender_id=tender_id,
            decision=matrix.decision,
            blockers=len(blockers),
        )
    except Exception as e:  # noqa: BLE001 — never fail the pipeline here
        state.setdefault("errors", []).append(f"Compliance evaluation failed: {e!s}"[:300])
        state["matrix"] = None
        state["decision"] = "UNKNOWN"
        observability.log_error("node_error", e, node="compliance_check", tender_id=tender_id)
    return state


def assess_blockers_node(state: ComplianceAgentState) -> ComplianceAgentState:
    """Flow decision point: Blockers? YES -> pause, NO -> proceed to P4."""
    tender_id = state.get("tender_id", "unknown")
    decision = state.get("decision", "UNKNOWN")
    observability.log_event(
        "node_success", node="assess_blockers", tender_id=tender_id, decision=decision
    )
    return state


def pause_checkpoint_node(state: ComplianceAgentState) -> ComplianceAgentState:
    """Mark the paused run; the service persists the checkpoint snapshot."""
    tender_id = state.get("tender_id", "unknown")
    matrix = state.get("matrix") or {}
    observability.log_event(
        "pipeline_paused",
        tender_id=tender_id,
        blockers=len(matrix.get("blockers", [])),
    )
    return state


def finalize_result_node(state: ComplianceAgentState) -> ComplianceAgentState:
    tender_id = state.get("tender_id", "unknown")
    observability.log_event(
        "node_success", node="finalize_result", tender_id=tender_id,
        decision=state.get("decision", "UNKNOWN"),
    )
    return state
