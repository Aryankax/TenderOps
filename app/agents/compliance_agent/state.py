from typing import Any

from typing_extensions import TypedDict


class ComplianceAgentState(TypedDict):
    tender_id: str
    analysis: dict[str, Any]
    evidence_map: dict[str, Any]
    matrix: dict[str, Any] | None
    decision: str  # PROCEED | PAUSE | UNKNOWN
    errors: list[str]
