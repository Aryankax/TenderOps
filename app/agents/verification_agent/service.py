from typing import Any

from app.agents.verification_agent.graph import verification_agent_graph
from app.agents.verification_agent.state import VerificationAgentState
from app.models.bid import VerificationReport
from app.services.observability import observability


class VerificationService:
    @staticmethod
    def verify(
        tender_id: str,
        requirements: list[dict[str, Any]],
        evidence_items: list[dict[str, Any]],
        drafts: list[dict[str, Any]],
        risk_report: list[str],
        blockers: list[dict[str, Any]] | None = None,
        compliance_items: list[dict[str, Any]] | None = None,
    ) -> VerificationReport:
        observability.log_event("verification_started", tender_id=tender_id)
        initial_state: VerificationAgentState = {
            "tender_id": tender_id,
            "requirements": requirements,
            "evidence_items": evidence_items,
            "drafts": drafts,
            "risk_report": risk_report or [],
            "blockers": blockers or [],
            "compliance_items": compliance_items or [],
            "report": None,
        }
        try:
            import neatlogs

            handler = neatlogs.langchain_handler()
            final_state = verification_agent_graph.invoke(
                initial_state, config={"callbacks": [handler]}
            )
            report = VerificationReport(**final_state["report"])
            observability.log_event(
                "verification_completed", tender_id=tender_id, passed=report.passed
            )
            return report
        except Exception as e:
            observability.log_error("verification_failed", e, tender_id=tender_id)
            raise RuntimeError(f"Verification failed: {e!s}")
