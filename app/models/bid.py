from pydantic import BaseModel, Field


class EvidenceRef(BaseModel):
    document_name: str
    page_number: int


class BidDraft(BaseModel):
    """One draft artifact. Grounded by construction: every draft cites the
    evidence it rests on, and numeric claims travel as structured fields
    so the verifier can re-check them without parsing prose."""

    draft_id: str  # D-<requirement_id> or D-GEN-<n>
    requirement_id: str | None = None
    kind: str = "requirement_response"
    # requirement_response | declaration | annexure_stub
    title: str
    body: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    claimed_value: float | None = None
    claimed_unit: str | None = None
    needs_human_signoff: bool = False


class VerificationFinding(BaseModel):
    check: str
    requirement_id: str | None = None
    draft_id: str | None = None
    passed: bool = True
    severity: str = "warning"  # blocking | warning
    detail: str = ""


class VerificationReport(BaseModel):
    tender_id: str
    passed: bool = False
    findings: list[VerificationFinding] = Field(default_factory=list)


class BidPackage(BaseModel):
    tender_id: str
    drafts: list[BidDraft] = Field(default_factory=list)
    checklist: list[str] = Field(default_factory=list)
    risk_report: list[str] = Field(default_factory=list)
