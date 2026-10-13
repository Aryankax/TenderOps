from typing import Any

from typing_extensions import TypedDict


class BidPrepAgentState(TypedDict):
    tender_id: str
    requirements: list[dict[str, Any]]  # draftable subset (COMPLIANT)
    all_requirements: list[dict[str, Any]]  # full set, for checklist/risk
    evidence_by_id: dict[str, list[dict[str, Any]]]
    compliance_by_id: dict[str, str]
    findings_to_fix: list[dict[str, Any]]
    drafts: list[dict[str, Any]]
    checklist: list[str]
    risk_report: list[str]
    warnings: list[str]
    final_package: dict[str, Any] | None
