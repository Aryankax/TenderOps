from enum import Enum

from pydantic import BaseModel, Field


class ComplianceStatus(str, Enum):
    """The four terminal states from the P3 plan (plain Python, no LLM)."""

    COMPLIANT = "COMPLIANT"
    NON_COMPLIANT = "NON_COMPLIANT"
    PENDING = "PENDING"  # waiting on documents/info, resumable
    NEEDS_REVIEW = "NEEDS_REVIEW"  # ambiguous — a human must decide


class BlockerType(str, Enum):
    MISSING_DOCUMENT = "MISSING_DOCUMENT"
    FAILED_CHECK = "FAILED_CHECK"
    EXPIRED_DOCUMENT = "EXPIRED_DOCUMENT"
    CONTRADICTION = "CONTRADICTION"
    UNRESOLVED_REVIEW = "UNRESOLVED_REVIEW"
    INCOMPLETE_EVIDENCE = "INCOMPLETE_EVIDENCE"


class RequirementCompliance(BaseModel):
    requirement_id: str
    requirement_title: str | None = None
    status: ComplianceStatus
    reasons: list[str] = Field(default_factory=list)
    evidence_status: str | None = None
    blocker_ids: list[str] = Field(default_factory=list)


class Blocker(BaseModel):
    blocker_id: str  # BLK-001, stable encounter order
    blocker_type: BlockerType
    description: str
    severity: str = "high"  # high | medium
    affected_requirement_ids: list[str] = Field(default_factory=list)
    resolution_hint: str = ""
    requires_human_review: bool = True


class ComplianceMatrix(BaseModel):
    tender_id: str
    items: list[RequirementCompliance] = Field(default_factory=list)
    blockers: list[Blocker] = Field(default_factory=list)
    # Human-overridden blockers: auditable, never silently dropped.
    waived_blockers: list[Blocker] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)
    decision: str = "PROCEED"  # PROCEED | PAUSE
