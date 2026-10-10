import pymupdf as fitz  # PyMuPDF
from typing import List, Optional, Dict
from pydantic import BaseModel
from app.core.sanitize import sanitize_text

class ParsedPage(BaseModel):
    page_number: int  # 1-based
    text: str
    extraction_status: str  # SUCCESS, NO_TEXT, ERROR

class DocumentParserResult(BaseModel):
    pages: List[ParsedPage]
    total_pages: int
    metadata: Dict
    warnings: List[str]

class DocumentParser:
    @staticmethod
    def parse_pdf(file_path: str) -> DocumentParserResult:
        warnings = []
        parsed_pages = []
        try:
            doc = fitz.open(file_path)
        except Exception as e:
            raise ValueError(f"Failed to open PDF: {e}")
        
        if doc.page_count == 0:
            raise ValueError("PDF has no pages.")
        
        for i in range(doc.page_count):
            page = doc[i]
            # Strip control chars from font-embedding artifacts (\x01, \x13…):
            # if the LLM copies them into a JSON string, Groq rejects the
            # whole response with 400 json_validate_failed.
            text = sanitize_text(page.get_text("text")).strip()
            
            status = "SUCCESS"
            if not text:
                status = "NO_TEXT"
                warnings.append(f"Page {i + 1} has no extractable text. May be scanned or empty.")
            
            parsed_pages.append(ParsedPage(
                page_number=i + 1,
                text=text,
                extraction_status=status
            ))
            
        metadata = doc.metadata or {}
        doc.close()
        
        return DocumentParserResult(
            pages=parsed_pages,
            total_pages=len(parsed_pages),
            metadata=metadata,
            warnings=warnings
        )
