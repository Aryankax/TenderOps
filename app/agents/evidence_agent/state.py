from typing import Any

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
