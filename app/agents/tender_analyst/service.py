import shutil
import tempfile
import uuid

from app.agents.tender_analyst.graph import tender_analyst_graph
from app.agents.tender_analyst.state import TenderAnalystState
from app.core.config import settings
from app.models.tender import TenderAnalysisResult
from app.services.observability import observability
from app.tools.document_parser import DocumentParser
from app.tools.link_crawler import crawl_tender_links, sanitize_manifest


class TenderAnalystService:
    @staticmethod
    def analyze_tender(
        file_path: str, document_name: str, fetch_links: bool = True
    ) -> TenderAnalysisResult:
        tender_id = str(uuid.uuid4())
        observability.log_event(
            "tender_analysis_started", tender_id=tender_id, document_name=document_name
        )

        try:
            parser_result = DocumentParser.parse_pdf(
                file_path, document_name=document_name
            )
            observability.log_event(
                "pdf_parsed", tender_id=tender_id, total_pages=parser_result.total_pages
            )
        except Exception as e:
            observability.log_error("pdf_parse_failed", e, tender_id=tender_id)
            raise ValueError(f"Failed to parse document: {e!s}")

        pages = [p.model_dump() for p in parser_result.pages]
        warnings = list(parser_result.warnings)
        linked_manifest: list[dict] = []
        if fetch_links:
            linked_manifest = TenderAnalystService._fetch_linked_documents(
                file_path, document_name, tender_id, pages, warnings
            )

        initial_state: TenderAnalystState = {
            "tender_id": tender_id,
            "document_name": document_name,
            "parsed_pages": pages,
            "warnings": warnings,
            "metadata": {},
            "requirements": [],
            "issues": [],
            "validation_errors": [],
            "failed_chunks": [],
            "linked_documents": linked_manifest,
            "retry_count": 0,
            "analysis_status": "PENDING",
            "final_result": None,
            "error_details": None,
        }

        try:
            import neatlogs

            handler = neatlogs.langchain_handler()
            final_state = tender_analyst_graph.invoke(
                initial_state, config={"callbacks": [handler]}
            )
            observability.log_event(
                "analysis_completed",
                tender_id=tender_id,
                status=final_state["analysis_status"],
            )
            return TenderAnalysisResult(**final_state["final_result"])
        except Exception as e:
            observability.log_error("analysis_failed", e, tender_id=tender_id)
            raise RuntimeError(f"Tender analysis failed: {e!s}")

    @staticmethod
    def _fetch_linked_documents(
        file_path: str,
        document_name: str,
        tender_id: str,
        pages: list,
        warnings: list,
    ) -> list[dict]:
        """BFS the tender's embedded links; parse fetched annexures as pages.

        Annex pages carry their own document_name so requirements keep
        attribution. Failures become manifest rows (reported downstream as
        missing attachments), never exceptions.
        """
        link_dir = tempfile.mkdtemp(prefix="tender_links_")
        try:
            manifest = crawl_tender_links(
                file_path,
                link_dir,
                max_depth=settings.TENDER_LINK_MAX_DEPTH,
                max_docs=settings.TENDER_LINK_MAX_DOCS,
                timeout_s=settings.TENDER_LINK_TIMEOUT_S,
                max_mb=settings.TENDER_LINK_MAX_MB,
            )
        except Exception as e:  # noqa: BLE001 — links are best-effort
            observability.log_error("link_crawl_failed", e, tender_id=tender_id)
            warnings.append(f"Linked-document crawl failed: {str(e)[:200]}")
            shutil.rmtree(link_dir, ignore_errors=True)
            return []
        try:
            for entry in manifest:
                if entry.get("status") != "fetched" or not entry.get("local_path"):
                    continue
                try:
                    annex = DocumentParser.parse_pdf(
                        entry["local_path"],
                        document_name=entry.get("filename", "linked-document.pdf"),
                    )
                    entry["pages"] = annex.total_pages
                    for p in annex.pages:
                        pages.append(p.model_dump())
                    warnings.extend(annex.warnings)
                except Exception as e:  # noqa: BLE001 — one bad annex
                    entry["status"] = "failed"
                    entry["reason"] = f"parse failed: {str(e)[:200]}"
                    entry.pop("local_path", None)
            fetched = sum(1 for m in manifest if m["status"] == "fetched")
            if manifest:
                observability.log_event(
                    "linked_docs_fetched",
                    tender_id=tender_id,
                    fetched=fetched,
                    total=len(manifest),
                )
                warnings.append(
                    f"Linked-document crawl: {fetched}/{len(manifest)} annexure(s) retrieved."
                )
            return sanitize_manifest(manifest)
        finally:
            shutil.rmtree(link_dir, ignore_errors=True)
