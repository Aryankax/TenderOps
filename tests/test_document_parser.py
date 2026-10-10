import pytest
import os
import tempfile
import pymupdf as fitz
from app.tools.document_parser import DocumentParser

def create_temp_pdf(text: str) -> str:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), text)
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    doc.save(path)
    doc.close()
    return path

def test_parse_pdf_success():
    path = create_temp_pdf("Hello, this is a test PDF.")
    try:
        result = DocumentParser.parse_pdf(path)
        assert result.total_pages == 1
        assert len(result.pages) == 1
        assert result.pages[0].page_number == 1
        assert "Hello, this is a test PDF." in result.pages[0].text
        assert result.pages[0].extraction_status == "SUCCESS"
    finally:
        os.remove(path)

def test_parse_pdf_no_text():
    # Create empty PDF
    doc = fitz.open()
    doc.new_page()
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    doc.save(path)
    doc.close()
    
    try:
        result = DocumentParser.parse_pdf(path)
        assert result.total_pages == 1
        assert result.pages[0].extraction_status == "NO_TEXT"
        assert "Page 1 has no extractable text" in result.warnings[0]
    finally:
        os.remove(path)

def test_parse_pdf_corrupt():
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "w") as f:
        f.write("Not a PDF file")
    
    try:
        with pytest.raises(ValueError, match="Failed to open PDF"):
            DocumentParser.parse_pdf(path)
    finally:
        os.remove(path)
