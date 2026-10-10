from typing import Dict, List, Any, Optional
from typing_extensions import TypedDict


class EvidenceAgentState(TypedDict):
    tender_id: str
    company_profile_id: str
    requirements: List[Dict[str, Any]]
    retrieved: Dict[str, List[Dict[str, Any]]]
    items: List[Dict[str, Any]]
    unmapped: List[str]
    warnings: List[str]
    final_result: Optional[Dict[str, Any]]
