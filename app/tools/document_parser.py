from typing import Any

import pymupdf as fitz  # PyMuPDF
from pydantic import BaseModel

from app.core.sanitize import sanitize_text


class ParsedPage(BaseModel):
    page_number: int  # 1-based
    text: str
    extraction_status: str  # SUCCESS, NO_TEXT, ERROR
    # Owning document ("" = the main uploaded file). Linked annexures carry
    # their own filename so multi-document requirements keep attribution.
    document_name: str = ""


def _ocr_page_to_text(page: "fitz.Page") -> str | None:
    """Best-effort OCR for scanned pages. None when unavailable/failed."""
    try:
        import shutil

        import pytesseract

        if shutil.which("tesseract") is None:
            return None
        from PIL import Image

        pix = page.get_pixmap(dpi=200)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        return pytesseract.image_to_string(img) or None
    except Exception:
        return None


class DocumentParserResult(BaseModel):
    pages: list[ParsedPage]
    total_pages: int
    metadata: dict[str, Any]
    warnings: list[str]


class DocumentParser:
    @staticmethod
    def parse_pdf(
        file_path: str, document_name: str = ""
    ) -> DocumentParserResult:
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
            raw_text = page.get_text("text")
            text = sanitize_text(raw_text if isinstance(raw_text, str) else "").strip()

            status = "SUCCESS"
            if not text:
                # Possibly a scanned page: try OCR before giving up, so
                # scanned annexures stay retrievable (idea-doc ingestion).
                ocr_text = _ocr_page_to_text(page)
                if ocr_text and sanitize_text(ocr_text).strip():
                    text = sanitize_text(ocr_text).strip()
                    warnings.append(
                        f"Page {i + 1} text recovered via OCR (scanned page)."
                    )
                else:
                    status = "NO_TEXT"
                    warnings.append(
                        f"Page {i + 1} has no extractable text. May be scanned or empty."
                        " (OCR unavailable: install Tesseract to read scanned pages.)"
                    )

            parsed_pages.append(
                ParsedPage(
                    page_number=i + 1,
                    text=text,
                    extraction_status=status,
                    document_name=document_name,
                )
            )

        metadata = doc.metadata or {}
        doc.close()

        return DocumentParserResult(
            pages=parsed_pages,
            total_pages=len(parsed_pages),
            metadata=metadata,
            warnings=warnings,
        )
