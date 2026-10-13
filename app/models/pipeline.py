from pydantic import BaseModel

from app.models.bid import BidPackage, VerificationReport
from app.models.compliance import ComplianceMatrix
from app.models.evidence import EvidenceMap
from app.models.tender import TenderAnalysisResult


class PipelineResult(BaseModel):
    tender_id: str
    analysis: TenderAnalysisResult
    evidence: EvidenceMap
    compliance: ComplianceMatrix | None = None
    package: BidPackage | None = None
    verification: VerificationReport | None = None
    # COMPLETED = ran to the end (verification may still have failed checks —
    # see verification.passed); PAUSED_ON_BLOCKERS = checkpoint saved, resume
    # with fixes. Execution outcome, separate from per-item compliance.
    pipeline_status: str = "COMPLETED"
    checkpoint_path: str | None = None
