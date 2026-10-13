from unittest.mock import MagicMock

import pymupdf
import pytest

from app.agents.evidence_agent import nodes as evidence_nodes
from app.agents.evidence_agent.llm_schemas import normalize_mapping
from app.agents.evidence_agent.retrieval import (
    _tokens_overlap,
    index_company_docs,
    query_evidence,
    reset_index,
)
from app.agents.evidence_agent.service import EvidenceAgentService
from app.core.config import settings
from app.models.evidence import EvidenceMap
from app.models.tender import TenderAnalysisResult


def _make_pdf(path: str, text: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(56, 56, 540, 780), text, fontsize=11)
    doc.save(path)
    doc.close()


@pytest.fixture()
def chroma_dir(tmp_path, monkeypatch):
    d = str(tmp_path / "chroma")
    monkeypatch.setattr(settings, "EVIDENCE_CHROMA_DIR", d)
    monkeypatch.setattr(settings, "EVIDENCE_MIN_SCORE", 0.0)
    yield d
    reset_index("sample")


@pytest.fixture()
def company_docs(tmp_path):
    docs = {
        "financials.pdf": "Audited annual turnover FY24: INR 12,10,00,000 (12.10 crore).",
        "iso.pdf": "ISO 9001:2015 valid from 01-04-2024 to 31-03-2027.",
    }
    out = []
    for fname, text in docs.items():
        p = str(tmp_path / fname)
        _make_pdf(p, text)
        out.append({"path": p, "name": fname})
    return out


def test_index_and_query_roundtrip(chroma_dir, company_docs):
    stats = index_company_docs(company_docs)
    assert stats == {"chunks": 2, "documents": 2}
    hits = query_evidence("annual turnover crore INR")
    assert hits, "expected at least one hit"
    assert hits[0]["document_name"] == "financials.pdf"
    assert hits[0]["page_number"] == 1
    assert "turnover" in hits[0]["excerpt"].lower()


def test_overlap_gate():
    assert _tokens_overlap(
        "Minimum average annual turnover 115637000",
        "Audited Financial Summary annual turnover 12.10 crore",
    )
    # Shared currency token alone must not pass the gate.
    assert not _tokens_overlap(
        "EMD amount earnest money deposit 9874275 INR",
        "Audited Financial Summary annual turnover INR 121000000",
    )
    # Inflections link without a stemmer.
    assert _tokens_overlap(
        "Three similar completed services", "successfully completed the work"
    )


def test_normalize_mapping_fallbacks():
    missing = normalize_mapping({}, "R-1", [])
    assert missing["status"] == "missing"
    assert missing["confidence"] == 0.0
    judged = normalize_mapping(
        {},
        "R-2",
        [{"document_name": "d.pdf", "page_number": 1, "excerpt": "x", "score": 0.5}],
    )
    assert judged["status"] == "needs_review"
    assert judged["hits"][0]["document_name"] == "d.pdf"


def _tender_result():
    return TenderAnalysisResult(
        tender_id="t-1",
        analysis_status="SUCCESS",
        metadata={},
        requirements=[
            {
                "requirement_id": "R-1",
                "category": "FINANCIAL_ELIGIBILITY",
                "title": "Minimum turnover",
                "original_text": "Minimum average annual turnover 1156.37 lakh",
                "threshold": {
                    "operator": ">=",
                    "value": 115637000.0,
                    "unit": "INR",
                    "original_value": "1156.37 lakh",
                },
                "source_references": [
                    {
                        "document_name": "t.pdf",
                        "page_number": 1,
                        "source_excerpt": "turnover clause",
                    }
                ],
            },
            {
                "requirement_id": "R-2",
                "category": "MANDATORY_DOCUMENTS",
                "title": "OEM authorization",
                "original_text": "OEM authorization certificate required",
                "source_references": [
                    {
                        "document_name": "t.pdf",
                        "page_number": 2,
                        "source_excerpt": "oem clause",
                    }
                ],
            },
        ],
        issues=[],
        total_pages=2,
        processed_pages=2,
        unreadable_pages=0,
        requirements_needing_review=0,
        warnings=[],
        failed_chunks=[],
        completeness={},
    )


def _fake_invoke(schema, prompt, operation, fallback_schemas=()):
    m = MagicMock()
    m.mappings = [
        {
            "requirement_id": "R-1",
            "status": "satisfied",
            "confidence": 0.9,
            "computed_value": 121000000.0,
            "computed_unit": "INR",
            "supporting_doc": "financials.pdf",
            "supporting_page": 1,
            "notes": "Turnover exceeds threshold.",
        }
    ]
    return m


def test_map_evidence_service(monkeypatch):
    monkeypatch.setattr(
        evidence_nodes,
        "query_evidence",
        lambda q, profile="sample", top_k=None, **kw: (
            [
                {
                    "document_name": "financials.pdf",
                    "page_number": 1,
                    "excerpt": "turnover 12.1 crore",
                    "score": 0.8,
                    "supports": "supports",
                }
            ]
            if "turnover" in q.lower()
            else []
        ),
    )
    monkeypatch.setattr(evidence_nodes, "_invoke_structured", _fake_invoke)
    result = EvidenceAgentService.map_evidence(_tender_result())
    assert isinstance(result, EvidenceMap)
    by_id = {i.requirement_id: i for i in result.items}
    assert by_id["R-1"].status == "satisfied"
    assert by_id["R-1"].requirement_title == "Minimum turnover"
    assert by_id["R-2"].requirement_title == "OEM authorization"
    assert by_id["R-1"].computed_value == 121000000.0
    assert by_id["R-1"].hits[0].document_name == "financials.pdf"
    # No hits -> missing without any LLM call.
    assert by_id["R-2"].status == "missing"
    assert result.unmapped_requirements == ["R-2"]
    # Original requirement order preserved.
    assert [i.requirement_id for i in result.items] == ["R-1", "R-2"]
