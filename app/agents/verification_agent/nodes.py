from app.agents.verification_agent.rules import verify_package
from app.agents.verification_agent.state import VerificationAgentState
from app.services.observability import observability


def verify_node(state: VerificationAgentState) -> VerificationAgentState:
    tender_id = state.get("tender_id", "unknown")
    observability.log_event("node_start", node="verify_package", tender_id=tender_id)
    try:
        report = verify_package(
            state.get("requirements", []),
            state.get("evidence_items", []),
            state.get("drafts", []),
            state.get("risk_report", []),
            state.get("blockers", []),
            state.get("compliance_items", []),
        )
        report.tender_id = tender_id
        state["report"] = report.model_dump()
        observability.log_event(
            "node_success", node="verify_package", tender_id=tender_id,
            passed=report.passed, findings=len(report.findings),
        )
    except Exception as e:  # noqa: BLE001 — verification must not crash runs
        observability.log_error("node_error", e, node="verify_package", tender_id=tender_id)
        state["report"] = {
            "tender_id": tender_id,
            "passed": False,
            "findings": [{
                "check": "verifier-error", "requirement_id": None, "draft_id": None,
                "passed": False, "severity": "blocking",
                "detail": f"Verifier crashed: {e!s}"[:300],
            }],
        }
    return state
