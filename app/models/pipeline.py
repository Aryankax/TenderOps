from pydantic import BaseModel

from app.models.evidence import EvidenceMap
from app.models.tender import TenderAnalysisResult


class PipelineResult(BaseModel):
    tender_id: str
    analysis: TenderAnalysisResult
    evidence: EvidenceMap
