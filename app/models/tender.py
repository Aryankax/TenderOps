from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RequirementCategory(str, Enum):
    FINANCIAL_ELIGIBILITY = "FINANCIAL_ELIGIBILITY"
    TECHNICAL_ELIGIBILITY = "TECHNICAL_ELIGIBILITY"
    EXPERIENCE = "EXPERIENCE"
    LEGAL_AND_STATUTORY = "LEGAL_AND_STATUTORY"
    MANDATORY_DOCUMENTS = "MANDATORY_DOCUMENTS"
    TECHNICAL_SPECIFICATIONS = "TECHNICAL_SPECIFICATIONS"
    COMMERCIAL = "COMMERCIAL"
    DEADLINES = "DEADLINES"
    SUBMISSION_INSTRUCTIONS = "SUBMISSION_INSTRUCTIONS"
    CONTRACTUAL = "CONTRACTUAL"
    OTHER = "OTHER"


class SemanticType(str, Enum):
    """What kind of clause a requirement is — distinct from its topic category.

    Used to stop treating every extracted clause as an unconditional bidder
    obligation: prohibitions keep their negation, buyer-discretion clauses are
    not bidder duties, and informational references carry no obligation.
    """

    BIDDER_OBLIGATION = "BIDDER_OBLIGATION"
    ELIGIBILITY_CONDITION = "ELIGIBILITY_CONDITION"
    EXEMPTION = "EXEMPTION"
    BUYER_DISCRETION = "BUYER_DISCRETION"
    PROHIBITION = "PROHIBITION"
    RESTRICTION = "RESTRICTION"
    CONTRACTUAL_TERM = "CONTRACTUAL_TERM"
    INFORMATIONAL = "INFORMATIONAL"


class RequirementThreshold(BaseModel):
    operator: str  # one of ">=", "<=", "=", "~" (approx), "unknown"
    value: float | None = (
        None  # None when no numeric value can be established — never 0.0 as a guess
    )
    unit: str
    original_value: str


class SourceReference(BaseModel):
    document_name: str
    page_number: int
    section_heading: str | None = None
    source_excerpt: str


class TenderRequirement(BaseModel):
    requirement_id: str
    category: RequirementCategory
    semantic_type: SemanticType | None = None
    title: str
    original_text: str
    normalized_description: str | None = None
    mandatory: bool | None = None
    applicability_condition: str | None = None
    threshold: RequirementThreshold | None = None
    required_evidence: list[str] = Field(default_factory=list)
    source_references: list[SourceReference] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    confidence: float | None = None
    needs_review: bool = False
    review_reason: str | None = None


class TenderMetadata(BaseModel):
    tender_title: str | None = None
    tender_reference: str | None = None
    issuing_authority: str | None = None
    submission_deadline: str | None = None
    bid_opening_date: str | None = None
    pre_bid_meeting_date: str | None = None
    clarification_deadline: str | None = None
    emd_amount: str | None = None
    tender_fee: str | None = None
    currency: str | None = None
    contract_duration: str | None = None
    delivery_deadline: str | None = None
    other_metadata: dict[str, Any] = Field(default_factory=dict)


class TenderAnalysisIssue(BaseModel):
    issue_id: str
    issue_type: str
    description: str
    severity: str
    affected_requirement_ids: list[str] = Field(default_factory=list)
    source_references: list[SourceReference] = Field(default_factory=list)
    requires_human_review: bool = True


class TenderAnalysisResult(BaseModel):
    tender_id: str
    analysis_status: str
    metadata: TenderMetadata
    requirements: list[TenderRequirement] = Field(default_factory=list)
    issues: list[TenderAnalysisIssue] = Field(default_factory=list)
    total_pages: int
    processed_pages: int
    unreadable_pages: int
    requirements_needing_review: int
    warnings: list[str] = Field(default_factory=list)
    failed_chunks: list[int] = Field(default_factory=list)
    completeness: dict[str, Any] = Field(default_factory=dict)
    analysis_version: str = "1.0"
