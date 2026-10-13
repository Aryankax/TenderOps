from typing import Any

from typing_extensions import TypedDict


class TenderAnalystState(TypedDict):
    tender_id: str
    document_name: str
    parsed_pages: list[dict[str, Any]]
    warnings: list[str]
    metadata: dict[str, Any] | None
    requirements: list[dict[str, Any]]
    issues: list[dict[str, Any]]
    validation_errors: list[str]
    failed_chunks: list[int]
    linked_documents: list[dict[str, Any]]
    retry_count: int
    analysis_status: str
    final_result: dict[str, Any] | None
    error_details: str | None
