from typing import Any, NotRequired

from typing_extensions import TypedDict


class EvidenceAgentState(TypedDict):
    tender_id: str
    company_profile_id: str
    requirements: list[dict[str, Any]]
    retrieved: dict[str, list[dict[str, Any]]]
    items: list[dict[str, Any]]
    unmapped: list[str]
    warnings: list[str]
    final_result: dict[str, Any] | None
    # Scoped re-check (resume flow): when set, retrieval searches ONLY these
    # indexed filenames (the fix documents attached to specific blockers)
    # instead of the whole company index.
    source_documents: NotRequired[list[str] | None]
