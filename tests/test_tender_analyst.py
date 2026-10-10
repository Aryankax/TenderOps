import pytest
import tempfile
import os
from unittest.mock import patch, MagicMock
from app.tools.document_parser import DocumentParser, ParsedPage
from app.agents.tender_analyst.service import TenderAnalystService
from app.models.tender import TenderAnalysisResult

# Mock the parser to not need a real PDF
class MockDocParser:
    @staticmethod
    def parse_pdf(file_path: str):
        if "empty" in file_path:
            raise ValueError("PDF has no pages.")
        if "corrupt" in file_path:
            raise ValueError("Failed to open PDF")
        
        return MagicMock(
            pages=[
                ParsedPage(page_number=1, text="Tender for IT Services.\\nSubmission Deadline: 30 November 2026", extraction_status="SUCCESS"),
                ParsedPage(page_number=2, text="Minimum annual turnover of ₹5 crore.", extraction_status="SUCCESS")
            ],
            total_pages=2,
            metadata={},
            warnings=[]
        )

# Mock LLM for metadata and requirements extraction
class MockLLM:
    def with_structured_output(self, model_cls, method="function_calling", **kwargs):
        mock_structured_llm = MagicMock()
        def mock_invoke(prompt):
            if "Extract the metadata" in prompt:
                # return mocked TenderMetadata
                return MagicMock(model_dump=lambda: {
                    "tender_title": "Tender for IT Services",
                    "submission_deadline": "30 November 2026"
                })
            else:
                # return mocked RequirementList
                req = MagicMock()
                req.model_dump.return_value = {
                    "requirement_id": "REQ-001",
                    "category": "FINANCIAL_ELIGIBILITY",
                    "title": "Turnover",
                    "original_text": "Minimum annual turnover of ₹5 crore.",
                    "normalized_description": "50000000 INR",
                    "mandatory": True,
                    "required_evidence": [],
                    "source_references": [
                        {"document_name": "test.pdf", "page_number": 2, "source_excerpt": "Minimum annual turnover of ₹5 crore."}
                    ],
                    "depends_on": [],
                    "needs_review": False
                }
                return MagicMock(requirements=[req])
        mock_structured_llm.invoke = mock_invoke
        return mock_structured_llm


@pytest.fixture
def mock_parser(monkeypatch):
    monkeypatch.setattr(DocumentParser, "parse_pdf", MockDocParser.parse_pdf)

@pytest.fixture
def mock_llm(monkeypatch):
    monkeypatch.setattr("app.agents.tender_analyst.nodes.get_llm", lambda: MockLLM())

def test_tender_analyst_service_success(mock_parser, mock_llm):
    result = TenderAnalystService.analyze_tender("test.pdf", "test.pdf")
    assert isinstance(result, TenderAnalysisResult)
    assert result.analysis_status == "SUCCESS"
    assert result.metadata.tender_title == "Tender for IT Services"
    assert result.metadata.submission_deadline == "30 November 2026"
    assert len(result.requirements) > 0
    assert result.requirements[0].normalized_description == "50000000 INR"

def test_tender_analyst_service_empty_pdf(mock_parser):
    with pytest.raises(ValueError, match="PDF has no pages."):
        TenderAnalystService.analyze_tender("empty.pdf", "empty.pdf")

def test_tender_analyst_service_corrupt_pdf(mock_parser):
    with pytest.raises(ValueError, match="Failed to open PDF"):
        TenderAnalystService.analyze_tender("corrupt.pdf", "corrupt.pdf")


def test_lenient_schema_absorbs_alias_drift_payloads():
    """Regression test for the production log failures.

    gpt-oss-20b emits alias field names (requirementId/id, description,
    thresholds/deadlines/evidence/page/section/note), string booleans
    ("mandatory"/"conditional") and string thresholds ("180 days"). The old
    strict RequirementList raised 45-50 validation errors per chunk; the
    lenient schema + normalizer must yield strict TenderRequirements.
    """
    from app.agents.tender_analyst.llm_schemas import (
        LLMRequirementList,
        normalize_requirement_list,
    )
    from app.models.tender import TenderRequirement

    # Exact shape of json_mode attempt 1 in the log (45-error case).
    drifted = {
        "requirements": [
            {
                "requirementId": "R1",
                "description": "Bid End Date/Time",
                "mandatory": "mandatory",
                "thresholds": [],
                "deadlines": ["24-10-2026 16:00:00"],
                "evidence": [],
                "page": 1,
                "section": "Bid Details",
            },
            {
                "requirementId": "R3",
                "description": "Minimum Average Annual Turnover",
                "mandatory": "mandatory",
                "thresholds": [{"value": "115637000", "currency": "INR"}],
                "deadlines": [],
                "evidence": ["Bidder Turnover Certificate"],
                "page": 1,
                "section": "Bid Details",
            },
            {
                "id": "R8",
                "description": "MSE Relaxation for Turnover",
                "mandatory": "conditional",
                "threshold": "115637000 INR (partial relaxation)",
                "page": "1",
                "section": "Bid Details",
            },
        ]
    }
    parsed = LLMRequirementList(**drifted)
    normalized = normalize_requirement_list(parsed, "GeM-Bidding-9978297.pdf", 1)
    assert len(normalized) == 3
    reqs = [TenderRequirement(**r) for r in normalized]
    assert reqs[0].requirement_id == "R1"
    assert reqs[0].mandatory is True
    assert "24-10-2026" in (reqs[0].applicability_condition or "")
    assert reqs[1].threshold.value == 115637000.0
    assert reqs[1].threshold.unit == "INR"
    assert reqs[1].required_evidence == ["Bidder Turnover Certificate"]
    assert reqs[2].mandatory is None
    assert reqs[2].needs_review is True
    # MSE relaxation is relief from a base requirement, not an obligation:
    # numeric framing withdrawn (value None), wording preserved verbatim.
    assert reqs[2].threshold.value is None
    assert reqs[2].threshold.original_value == "115637000 INR (partial relaxation)"
    assert "Partial exemption scope" in (reqs[2].review_reason or "")


def test_structured_invoke_falls_back_to_raw_json(monkeypatch):
    """function_calling 'did not call a tool' + json_mode parse failure must
    recover via the raw-JSON fallback instead of failing the chunk."""
    from app.agents.tender_analyst import nodes as nodes_mod
    from app.agents.tender_analyst.llm_schemas import LLMRequirementList

    def fake_wso(schema, method="json_mode", **kwargs):
        m = MagicMock()
        if method == "function_calling":
            m.invoke = MagicMock(
                side_effect=Exception(
                    "400 tool_use_failed: Tool choice is required, "
                    "but model did not call a tool"
                )
            )
        else:
            m.invoke = MagicMock(
                side_effect=Exception("Failed to parse RequirementList")
            )
        return m

    raw = (
        '{"requirements": [{"requirement_id": "R1", "category": "DEADLINES", '
        '"title": "Bid End", "original_text": "Bid ends 24-10-2026", '
        '"mandatory": true}]}'
    )
    fake_llm = MagicMock()
    fake_llm.with_structured_output = fake_wso
    fake_llm.invoke = MagicMock(return_value=MagicMock(content=raw))
    monkeypatch.setattr(nodes_mod, "get_llm", lambda: fake_llm)

    out = nodes_mod._invoke_structured(
        LLMRequirementList, "prompt", operation="test_chain"
    )
    assert len(out.requirements) == 1
    assert out.requirements[0].requirement_id == "R1"


def test_lenient_metadata_coerces_numeric_amounts():
    """Regression test: model returns numbers for amount fields
    (log: "emd_amount": 9874275). Must coerce, not raise string_type."""
    from app.agents.tender_analyst.llm_schemas import LLMMetadata, normalize_metadata

    parsed = LLMMetadata(
        tender_title="Hiring of Agency for IT Projects- Milestone basis",
        tender_reference="GEM/2026/B/8111208",
        emd_amount=9874275,
        tender_fee=1000,
        currency="INR",
    )
    normed = normalize_metadata(parsed)
    assert normed["emd_amount"] == "9874275"
    assert normed["tender_fee"] == "1000"


def test_flat_schema_primary_path():
    """Flat scalar-only payloads (the new primary schema) must normalize to
    strict requirements, with source_references and thresholds built in code."""
    from app.agents.tender_analyst.llm_schemas import (
        LLMSimpleRequirementList,
        normalize_requirement_list,
    )
    from app.models.tender import TenderRequirement

    flat = {
        "requirements": [
            {
                "requirement_id": "R1",
                "category": "DEADLINES",
                "title": "Bid end date",
                "original_text": "Bid ends on 24-10-2026 16:00:00",
                "mandatory": True,
                "deadline": "24-10-2026 16:00:00",
                "page": 1,
                "section": "Bid Details",
            },
            {
                "requirementId": "R6",
                "description": "MSE Relaxation",
                "mandatory": "conditional",
                "threshold_text": "115637000 INR (partial relaxation)",
                "page": "1",
                "section": "Bid Details",
            },
        ]
    }
    normed = normalize_requirement_list(
        LLMSimpleRequirementList(**flat), "d.pdf", 1
    )
    assert len(normed) == 2
    reqs = [TenderRequirement(**r) for r in normed]
    assert reqs[0].source_references[0].page_number == 1
    assert reqs[0].source_references[0].section_heading == "Bid Details"
    assert "24-10-2026" in (reqs[0].applicability_condition or "")
    # MSE relaxation reframed as relief (value withdrawn, wording kept).
    assert reqs[1].threshold.value is None
    assert reqs[1].threshold.operator == "unknown"
    assert reqs[1].mandatory is None
    assert reqs[1].needs_review is True


def test_staged_fallback_uses_second_schema(monkeypatch):
    """json_validate_failed on the primary schema must fall through to the
    fallback schema stage instead of failing the chunk."""
    from app.agents.tender_analyst import nodes as nodes_mod
    from app.agents.tender_analyst.llm_schemas import (
        LLMRequirementList,
        LLMSimpleRequirementList,
    )

    def fake_wso(schema, method="json_mode", **kwargs):
        m = MagicMock()
        if schema is LLMSimpleRequirementList:
            m.invoke = MagicMock(
                side_effect=Exception(
                    "Failed to validate JSON. Please adjust your prompt."
                )
            )
        else:
            m.invoke = MagicMock(
                return_value=LLMRequirementList(
                    requirements=[
                        {
                            "requirement_id": "R9",
                            "category": "OTHER",
                            "title": "T",
                            "original_text": "O",
                        }
                    ]
                )
            )
        return m

    fake_llm = MagicMock()
    fake_llm.with_structured_output = fake_wso
    monkeypatch.setattr(nodes_mod, "get_llm", lambda: fake_llm)

    out = nodes_mod._invoke_structured(
        LLMSimpleRequirementList,
        "prompt",
        operation="test_stages",
        fallback_schemas=(LLMRequirementList,),
    )
    assert isinstance(out, LLMRequirementList)
    assert out.requirements[0].requirement_id == "R9"


def test_sanitize_strips_control_chars():
    from app.core.sanitize import sanitize_text

    dirty = "prefix \x01 mid \x13 end\nLine2\x7f"
    assert sanitize_text(dirty) == "prefix  mid  end\nLine2"
    assert sanitize_text("") == ""
    assert sanitize_text(None) == ""


def test_extract_json_object_repairs_production_glitches():
    """Exact glitch shapes from the chunk-5 failed_generation payloads."""
    from app.agents.tender_analyst.nodes import _extract_json_object

    # Control bytes copied from PDF text + doubled comma (attempt 2).
    d = _extract_json_object(
        '{"requirements": [{"requirement_id": "R7", '
        '"original_text": "abc \x01 def",,\"mandatory": null}]}'
    )
    assert d["requirements"][0]["requirement_id"] == "R7"
    # Doubled colon '"evidence"::null' (fallback attempt 1).
    d = _extract_json_object(
        '{"requirements": [{"requirement_id": "R6", "title": "T", '
        '"original_text": "O", "evidence"::null}]}'
    )
    assert d["requirements"][0]["requirement_id"] == "R6"
    # Trailing commas repaired; URLs and prose commas untouched.
    assert _extract_json_object('{"a": 1, "b": [1, 2,],}') == {"a": 1, "b": [1, 2]}
    d = _extract_json_object(
        '{"requirements": [{"requirement_id": "R1", '
        '"title": "See http://example.com/a,b", "original_text": "O"}]}'
    )
    assert d["requirements"][0]["title"] == "See http://example.com/a,b"


def test_normalized_outputs_contain_no_control_chars():
    from app.agents.tender_analyst.llm_schemas import normalize_requirement

    n = normalize_requirement(
        {
            "requirement_id": "R1",
            "category": "OTHER",
            "title": "T\x01",
            "original_text": "O\x13X",
            "source_references": [
                {"document_name": "d", "page_number": 1, "source_excerpt": "E\x01"}
            ],
        },
        "d.pdf",
        1,
    )
    assert n["title"] == "T"
    assert n["original_text"] == "OX"
    assert n["source_references"][0]["source_excerpt"] == "E"


def test_rendered_prompt_contains_valid_example_json():
    """The few-shot example must render as valid, comma-separated JSON after
    .format() — the model copies its shape verbatim."""
    import json
    from app.agents.tender_analyst.prompts import REQUIREMENTS_EXTRACTION_PROMPT

    rendered = REQUIREMENTS_EXTRACTION_PROMPT.format(
        document_name="d.pdf", chunk_content="--- Page 1 ---\nSome text"
    )
    assert "{{" not in rendered and "}}" not in rendered
    start = rendered.index('{"requirement_id": "R1"')
    depth = 0
    for end, ch in enumerate(rendered[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
    example = json.loads(rendered[start : end + 1])
    assert example["requirement_id"] == "R1"
    assert example["mandatory"] is True
    assert example["semantic_type"] == "ELIGIBILITY_CONDITION"
    assert set(example) == {
        "requirement_id", "category", "semantic_type", "title", "original_text",
        "mandatory", "condition", "threshold_text", "evidence",
        "deadline", "page", "section",
    }


