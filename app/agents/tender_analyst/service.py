import uuid
from typing import List, Dict, Any
from app.tools.document_parser import DocumentParser
from app.agents.tender_analyst.graph import tender_analyst_graph
from app.services.observability import observability
from app.models.tender import TenderAnalysisResult

class TenderAnalystService:
    @staticmethod
    def analyze_tender(file_path: str, document_name: str) -> TenderAnalysisResult:
        tender_id = str(uuid.uuid4())
        observability.log_event("tender_analysis_started", tender_id=tender_id, document_name=document_name)
        
        try:
            parser_result = DocumentParser.parse_pdf(file_path)
            observability.log_event("pdf_parsed", tender_id=tender_id, total_pages=parser_result.total_pages)
        except Exception as e:
            observability.log_error("pdf_parse_failed", e, tender_id=tender_id)
            raise ValueError(f"Failed to parse document: {str(e)}")

        initial_state = {
            "tender_id": tender_id,
            "document_name": document_name,
            "parsed_pages": [p.model_dump() for p in parser_result.pages],
            "warnings": parser_result.warnings,
            "metadata": {},
            "requirements": [],
            "issues": [],
            "validation_errors": [],
            "failed_chunks": [],
            "retry_count": 0,
            "analysis_status": "PENDING",
            "final_result": None,
            "error_details": None
        }

        try:
            import neatlogs
            handler = neatlogs.langchain_handler()
            final_state = tender_analyst_graph.invoke(initial_state, config={"callbacks": [handler]})
            observability.log_event("analysis_completed", tender_id=tender_id, status=final_state["analysis_status"])
            return TenderAnalysisResult(**final_state["final_result"])
        except Exception as e:
            observability.log_error("analysis_failed", e, tender_id=tender_id)
            raise RuntimeError(f"Tender analysis failed: {str(e)}")
