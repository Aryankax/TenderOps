import os

from app.agents.evidence_agent.graph import evidence_agent_graph
from app.agents.evidence_agent.retrieval import index_company_docs
from app.agents.evidence_agent.state import EvidenceAgentState
from app.models.evidence import EvidenceMap
from app.models.tender import TenderAnalysisResult
from app.services.observability import observability


class EvidenceAgentService:
    @staticmethod
    def build_index(
        docs_dir: str, company_profile_id: str = "sample"
    ) -> dict[str, int]:
        docs: list[dict[str, str]] = []
        for fname in sorted(os.listdir(docs_dir)):
            if fname.lower().endswith(".pdf"):
                docs.append({"path": os.path.join(docs_dir, fname), "name": fname})
        return index_company_docs(docs, company_profile_id)

    @staticmethod
    def map_evidence(
        tender_result: TenderAnalysisResult,
        company_profile_id: str = "sample",
        requirement_ids: list[str] | None = None,
        source_documents: list[str] | None = None,
    ) -> EvidenceMap:
        """Map evidence, optionally for a SUBSET of requirements (P3 resume:
        only affected checks re-run) and ONLY inside given source documents
        (resume fix files attached to specific blockers)."""
        requirements = [r.model_dump() for r in tender_result.requirements]
        if requirement_ids is not None:
            wanted = set(requirement_ids)
            requirements = [
                r for r in requirements if str(r.get("requirement_id")) in wanted
            ]
        observability.log_event(
            "evidence_mapping_started",
            tender_id=tender_result.tender_id,
            requirements=len(requirements),
            scoped_documents=source_documents or [],
        )
        initial_state: EvidenceAgentState = {
            "tender_id": tender_result.tender_id,
            "company_profile_id": company_profile_id,
            "requirements": requirements,
            "retrieved": {},
            "items": [],
            "unmapped": [],
            "warnings": [],
            "final_result": None,
            "source_documents": source_documents,
        }
        try:
            import neatlogs

            handler = neatlogs.langchain_handler()
            final_state = evidence_agent_graph.invoke(
                initial_state, config={"callbacks": [handler]}
            )
            return EvidenceMap(**final_state["final_result"])
        except Exception as e:
            observability.log_error(
                "evidence_mapping_failed", e, tender_id=tender_result.tender_id
            )
            raise RuntimeError(f"Evidence mapping failed: {e!s}")
