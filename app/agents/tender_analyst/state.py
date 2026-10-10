from typing import List, Dict, Any, Optional
from typing_extensions import TypedDict

class TenderAnalystState(TypedDict):
    tender_id: str
    document_name: str
    parsed_pages: List[Dict[str, Any]]
    warnings: List[str]
    metadata: Optional[Dict[str, Any]]
    requirements: List[Dict[str, Any]]
    issues: List[Dict[str, Any]]
    validation_errors: List[str]
    failed_chunks: List[int]
    retry_count: int
    analysis_status: str
    final_result: Optional[Dict[str, Any]]
    error_details: Optional[str]
