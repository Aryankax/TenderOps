from pydantic import BaseModel, Field
from enum import Enum
from typing import List, Optional, Any

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

class RequirementThreshold(BaseModel):
    operator: str
    value: float
    unit: str
    original_value: str

class SourceReference(BaseModel):
    document_name: str
    page_number: int
    section_heading: Optional[str] = None
    source_excerpt: str

class TenderRequirement(BaseModel):
    requirement_id: str
    category: RequirementCategory
    title: str
    original_text: str
    normalized_description: Optional[str] = None
    mandatory: Optional[bool] = None
    applicability_condition: Optional[str] = None
    threshold: Optional[RequirementThreshold] = None
    required_evidence: List[str] = Field(default_factory=list)
    source_references: List[SourceReference] = Field(default_factory=list)
    depends_on: List[str] = Field(default_factory=list)
    confidence: Optional[float] = None
    needs_review: bool = False
    review_reason: Optional[str] = None

class TenderMetadata(BaseModel):
    tender_title: Optional[str] = None
    tender_reference: Optional[str] = None
    issuing_authority: Optional[str] = None
    submission_deadline: Optional[str] = None
    bid_opening_date: Optional[str] = None
    pre_bid_meeting_date: Optional[str] = None
    clarification_deadline: Optional[str] = None
    emd_amount: Optional[str] = None
    tender_fee: Optional[str] = None
    currency: Optional[str] = None
    contract_duration: Optional[str] = None
    delivery_deadline: Optional[str] = None
    other_metadata: dict = Field(default_factory=dict)

class TenderAnalysisIssue(BaseModel):
    issue_id: str
    issue_type: str
    description: str
    severity: str
    affected_requirement_ids: List[str] = Field(default_factory=list)
    source_references: List[SourceReference] = Field(default_factory=list)
    requires_human_review: bool = True

class TenderAnalysisResult(BaseModel):
    tender_id: str
    analysis_status: str
    metadata: TenderMetadata
    requirements: List[TenderRequirement] = Field(default_factory=list)
    issues: List[TenderAnalysisIssue] = Field(default_factory=list)
    total_pages: int
    processed_pages: int
    unreadable_pages: int
    requirements_needing_review: int
    warnings: List[str] = Field(default_factory=list)
    analysis_version: str = "1.0"
