from pydantic import BaseModel, Field


class EvidenceHit(BaseModel):
    document_name: str
    page_number: int
    excerpt: str
    score: float  # retrieval similarity 0..1
    supports: str = "supports"  # supports | partial | contradicts


class RequirementEvidence(BaseModel):
    requirement_id: str
    requirement_title: str | None = None
    status: str  # satisfied | partial | missing | needs_review
    confidence: float = 0.0
    hits: list[EvidenceHit] = Field(default_factory=list)
    computed_value: float | None = None
    computed_unit: str | None = None
    notes: str | None = None


class EvidenceMap(BaseModel):
    tender_id: str
    company_profile_id: str = "sample"
    items: list[RequirementEvidence] = Field(default_factory=list)
    unmapped_requirements: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
