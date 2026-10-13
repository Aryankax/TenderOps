from typing import Any

from app.agents.bidprep_agent.graph import bidprep_agent_graph
from app.agents.bidprep_agent.state import BidPrepAgentState
from app.models.bid import BidPackage
from app.models.compliance import ComplianceMatrix
from app.models.evidence import EvidenceMap
from app.models.tender import TenderAnalysisResult
from app.services.observability import observability


class BidPrepService:
    @staticmethod
    def build_package(
        analysis: TenderAnalysisResult,
        evidence: EvidenceMap,
        compliance: ComplianceMatrix,
        requirement_ids: list[str] | None = None,
        findings_to_fix: list[dict[str, Any]] | None = None,
    ) -> BidPackage:
        """Draft responses for COMPLIANT requirements + checklist/risk.

        requirement_ids restricts drafting (orchestrator fix-loop re-drafts
        only failed drafts); checklist/risk always cover the full set.
        """
        comp_by_id = {i.requirement_id: i.status.value for i in compliance.items}
        ev_by_id: dict[str, list[dict[str, Any]]] = {}
        for item in evidence.items:
            ev_by_id[item.requirement_id] = [
                h.model_dump() for h in item.hits
            ]
        wanted = set(requirement_ids) if requirement_ids is not None else None
        all_reqs = [r.model_dump() for r in analysis.requirements]
        draftable = [
            r for r in all_reqs
            if comp_by_id.get(r["requirement_id"]) == "COMPLIANT"
            and (wanted is None or r["requirement_id"] in wanted)
        ]
        observability.log_event(
            "bidprep_started", tender_id=analysis.tender_id,
            draftable=len(draftable),
        )
        initial_state: BidPrepAgentState = {
            "tender_id": analysis.tender_id,
            "requirements": draftable,
            "all_requirements": all_reqs,
            "evidence_by_id": ev_by_id,
            "compliance_by_id": comp_by_id,
            "findings_to_fix": findings_to_fix or [],
            "drafts": [],
            "checklist": [],
            "risk_report": [],
            "warnings": [],
            "final_package": None,
        }
        try:
            import neatlogs

            handler = neatlogs.langchain_handler()
            final_state = bidprep_agent_graph.invoke(
                initial_state, config={"callbacks": [handler]}
            )
            package = BidPackage(
                tender_id=analysis.tender_id,
                drafts=final_state["drafts"],
                checklist=final_state["checklist"],
                risk_report=final_state["risk_report"],
            )
            # Blocker-derived risks ride along so nothing unresolved is silent.
            for blocker in compliance.blockers:
                entry = (
                    f"{blocker.blocker_id} ({blocker.blocker_type.value}): "
                    f"{blocker.description}"
                )
                if entry not in package.risk_report:
                    package.risk_report.append(entry)
            observability.log_event(
                "bidprep_completed", tender_id=analysis.tender_id,
                drafts=len(package.drafts),
            )
            return package
        except Exception as e:
            observability.log_error(
                "bidprep_failed", e, tender_id=analysis.tender_id
            )
            raise RuntimeError(f"Bid preparation failed: {e!s}")
