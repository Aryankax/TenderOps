from pydantic import BaseModel, Field
from typing import List, Optional


class EvidenceHit(BaseModel):
    document_name: str
    page_number: int
    excerpt: str
    score: float  # retrieval similarity 0..1
    supports: str = "supports"  # supports | partial | contradicts


class RequirementEvidence(BaseModel):
    requirement_id: str
    status: str  # satisfied | partial | missing | needs_review
    confidence: float = 0.0
    hits: List[EvidenceHit] = Field(default_factory=list)
    computed_value: Optional[float] = None
    computed_unit: Optional[str] = None
    notes: Optional[str] = None


class EvidenceMap(BaseModel):
    tender_id: str
    company_profile_id: str = "sample"
    items: List[RequirementEvidence] = Field(default_factory=list)
    unmapped_requirements: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
