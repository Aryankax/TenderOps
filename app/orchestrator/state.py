from typing import Any

from typing_extensions import TypedDict


class OrchestratorState(TypedDict):
    run_id: str
    tender_id: str  # set by P1 (it mints the analysis id)
    document_name: str
    company_profile_id: str
    tender_pdf_path: str
    fetch_links: bool
    analysis: dict[str, Any] | None
    evidence: dict[str, Any] | None
    compliance: dict[str, Any] | None
    package: dict[str, Any] | None
    verification: dict[str, Any] | None
    fix_attempts: int
    pipeline_status: str  # RUNNING | PAUSED_ON_BLOCKERS | COMPLETED
    checkpoint_path: str | None
    errors: list[str]
