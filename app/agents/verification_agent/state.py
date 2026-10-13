from typing import Any

from typing_extensions import TypedDict


class VerificationAgentState(TypedDict):
    tender_id: str
    requirements: list[dict[str, Any]]
    evidence_items: list[dict[str, Any]]
    drafts: list[dict[str, Any]]
    risk_report: list[str]
    blockers: list[dict[str, Any]]
    compliance_items: list[dict[str, Any]]
    report: dict[str, Any] | None
