"""Quality-improvement regression tests (P1-P8) for the Tender Analyst Agent.

Covers: globally unique IDs across chunks, numeric normalization (min/max/
unknown/lakh-crore), normalized descriptions, missing-attachment and
contradiction detection, semantic classification of negative/conditional
clauses, review-count consistency, and partial-result preservation.
"""

from unittest.mock import MagicMock

from app.models.tender import SemanticType


def _norm(items, doc="d.pdf", page=1, start=0):
    from app.agents.tender_analyst.llm_schemas import normalize_requirement_list
    return normalize_requirement_list({"requirements": items}, doc, page, start_index=start)


def test_requirement_ids_unique_across_chunks():
    """P1: model restarts R1..Rn every chunk — merged IDs must be unique,
    first occurrences stable, auto IDs globally sequential."""
    from app.agents.tender_analyst.llm_schemas import (
        deduplicate_requirements,
        ensure_unique_ids,
    )

    chunk_a = _norm([
        {"requirement_id": "R1", "category": "DEADLINES", "title": "Bid end date",
         "original_text": "Bid ends 24-10-2026", "mandatory": True, "page": 1},
        {"category": "OTHER", "title": "Untitled clause",
         "original_text": "Some generic clause text here", "page": 1},
    ], start=0)
    chunk_b = _norm([
        {"requirement_id": "R1", "category": "CONTRACTUAL", "title": "Option clause",
         "original_text": "Buyer may vary quantity up to 25%", "mandatory": None, "page": 2},
        {"category": "OTHER", "title": "Another clause",
         "original_text": "More generic clause text here", "page": 2},
    ], start=2)
    merged, dropped = deduplicate_requirements(chunk_a + chunk_b)
    assert dropped == []
    final = ensure_unique_ids(merged)
    ids = [r["requirement_id"] for r in final]
    assert len(ids) == len(set(ids)) == 4
    assert ids[0] == "R1"  # first occurrence stable
    assert ids[2] == "R1-2"  # collision disambiguated deterministically
    assert ids[1] == "R-2" and ids[3] == "R-4"  # auto IDs sequential, not restarted


def test_dedup_keeps_first_and_reports_dropped():
    from app.agents.tender_analyst.llm_schemas import (
        deduplicate_requirements,
        ensure_unique_ids,
    )

    dup = {"requirement_id": "R1", "category": "DEADLINES", "title": "Bid end",
           "original_text": "Bid ends 24-10-2026", "mandatory": True, "page": 1}
    other = {"requirement_id": "R2", "category": "DEADLINES", "title": "Bid end",
             "original_text": "Bid ends 24-10-2026", "mandatory": True, "page": 2}
    merged, dropped = deduplicate_requirements(_norm([dup, other]))
    assert dropped == ["R2"]
    final = ensure_unique_ids(merged)
    assert [r["requirement_id"] for r in final] == ["R1"]


def test_minimum_turnover_threshold():
    """P2: minimum wording -> >= with correct scaled value, original kept."""
    reqs = _norm([{
        "requirement_id": "R1", "category": "FINANCIAL_ELIGIBILITY",
        "title": "Minimum average annual turnover",
        "original_text": "Minimum average annual turnover of Rs. 5 crore for last 3 years",
        "mandatory": True,
        "threshold_text": "Rs. 5 crore",
        "page": 1,
    }])
    th = reqs[0]["threshold"]
    assert th["operator"] == ">="
    assert th["value"] == 50000000.0
    assert th["unit"] == "INR"
    assert th["original_value"] == "Rs. 5 crore"
    assert reqs[0]["needs_review"] is False


def test_maximum_threshold_operator():
    reqs = _norm([{
        "requirement_id": "R1", "category": "CONTRACTUAL",
        "title": "Option clause cap",
        "original_text": "Quantity may be increased up to 25 percent",
        "mandatory": None,
        "threshold_text": "up to 25 percent",
        "page": 6,
    }])
    th = reqs[0]["threshold"]
    assert th["operator"] == "<="
    assert th["value"] == 25.0


def test_unknown_threshold_never_zero():
    """P2: unquantified thresholds keep value None (never 0.0) and are flagged."""
    reqs = _norm([{
        "requirement_id": "R8", "category": "CONTRACTUAL",
        "title": "MSE relaxation",
        "original_text": "Partial relaxation applies to MSEs as per rules",
        "mandatory": "conditional",
        "threshold_text": "partial relaxation",
        "page": 1,
    }])
    th = reqs[0]["threshold"]
    assert th is not None
    assert th["value"] is None  # must NOT default to 0.0
    assert th["value"] != 0.0
    assert th["operator"] == "unknown"
    assert reqs[0]["needs_review"] is True
    assert "no parseable numeric value" in (reqs[0]["review_reason"] or "")


def test_lakh_scaling():
    reqs = _norm([{
        "requirement_id": "R1", "category": "FINANCIAL_ELIGIBILITY",
        "title": "Minimum turnover",
        "original_text": "Minimum average annual turnover 1156.37 Lakh",
        "mandatory": True, "threshold_text": "1156.37 Lakh", "page": 1,
    }])
    th = reqs[0]["threshold"]
    assert th["value"] == 115637000.0
    assert th["unit"] == "INR"
    assert th["original_value"] == "1156.37 Lakh"


def test_missing_attachment_detection():
    """P4: buyer-side doc refs raise MISSING_ATTACHMENT; bidder evidence does not."""
    from app.agents.tender_analyst.issues import detect_issues

    reqs = _norm([
        {"requirement_id": "R1", "category": "MANDATORY_DOCUMENTS",
         "title": "Buyer Added Bid Specific ATC",
         "original_text": "Buyer uploaded ATC document Click here to view the file.",
         "mandatory": True, "evidence": "ATC document", "page": 6},
        {"requirement_id": "R2", "category": "MANDATORY_DOCUMENTS",
         "title": "Escalation matrix",
         "original_text": "Bidder must provide escalation matrix.",
         "mandatory": True, "evidence": "Escalation Matrix", "page": 6},
    ])
    issues = detect_issues(reqs, {}, "d.pdf")
    by_type = {}
    for i in issues:
        by_type.setdefault(i["issue_type"], []).append(i)
    assert "MISSING_ATTACHMENT" in by_type
    affected = {r for i in by_type["MISSING_ATTACHMENT"] for r in i["affected_requirement_ids"]}
    assert "R1" in affected
    assert "R2" not in affected  # bidder evidence is not a missing attachment
    assert all(i["requires_human_review"] for i in issues)
    assert all(i["issue_id"].startswith("ISSUE-") for i in issues)


def test_negative_and_conditional_clauses():
    """P5: prohibitions keep negation + stay binding; conditionals stay conditional."""
    reqs = _norm([
        {"requirement_id": "R1", "category": "LEGAL_AND_STATUTORY",
         "title": "Malicious code prohibition",
         "original_text": "The hardware must not contain embedded malicious code.",
         "mandatory": True, "page": 6},
        {"requirement_id": "R2", "category": "CONTRACTUAL",
         "title": "Lumpsum scope increase",
         "original_text": "Scope may increase up to 25% for lumpsum contracts",
         "mandatory": None,
         "condition": "Applicable only to lumpsum-based service contracts",
         "page": 6},
    ])
    assert reqs[0]["semantic_type"] == SemanticType.PROHIBITION.value
    assert reqs[0]["mandatory"] is True
    assert "must not" in reqs[0]["original_text"].lower()
    assert "must not" in (reqs[0]["normalized_description"] or "").lower() \
        or "Prohibition" in (reqs[0]["normalized_description"] or "")
    assert reqs[1]["mandatory"] is None
    assert reqs[1]["needs_review"] is True
    assert "Conditional requirement" in (reqs[1]["review_reason"] or "")


def test_buyer_discretion_is_not_bidder_obligation():
    reqs = _norm([{
        "requirement_id": "R1", "category": "CONTRACTUAL",
        "title": "Buyer quantity variation",
        "original_text": "The buyer may increase or decrease the quantity at its discretion.",
        "mandatory": None, "page": 6,
    }])
    assert reqs[0]["semantic_type"] == SemanticType.BUYER_DISCRETION.value
    assert reqs[0]["mandatory"] is False


def test_contradictory_deadlines():
    """P4: submission after opening + same-topic threshold conflicts flagged."""
    from app.agents.tender_analyst.issues import detect_issues

    reqs = _norm([
        {"requirement_id": "R1", "category": "FINANCIAL_ELIGIBILITY",
         "title": "Minimum turnover",
         "original_text": "Minimum turnover of 100 lakh",
         "mandatory": True, "threshold_text": "minimum 100 lakh", "page": 1},
        {"requirement_id": "R2", "category": "FINANCIAL_ELIGIBILITY",
         "title": "Bidder turnover requirement",
         "original_text": "Bidder should have turnover of 50 lakh",
         "mandatory": True, "threshold_text": "minimum 50 lakh", "page": 2},
    ])
    issues = detect_issues(
        reqs,
        {"submission_deadline": "24-10-2026 16:00:00", "bid_opening_date": "25-10-2025 16:00:00"},
        "d.pdf",
    )
    contras = [i for i in issues if i["issue_type"] == "CONTRADICTION"]
    assert len(contras) == 2
    descs = " | ".join(i["description"] for i in contras)
    assert "R1" in descs and "R2" in descs
    assert "submission deadline" in descs.lower()


def test_normalized_descriptions_populated_when_supported():
    """P3: reliable cases get descriptions restating facts; unreliable -> None + flag."""
    reqs = _norm([
        {"requirement_id": "R1", "category": "FINANCIAL_ELIGIBILITY",
         "title": "Minimum average annual turnover",
         "original_text": "Minimum average annual turnover of Rs. 5 crore",
         "mandatory": True, "threshold_text": "Rs. 5 crore",
         "evidence": "Bidder Turnover Certificate", "page": 1},
        {"requirement_id": "R2", "category": "OTHER",
         "title": "", "original_text": "", "page": 1},
    ])
    d = reqs[0]["normalized_description"]
    assert d is not None
    assert "50,000,000" in d and "INR" in d
    assert "Bidder Turnover Certificate" in d
    # Original wording/monetary expression preserved alongside, not replaced.
    assert reqs[0]["original_text"] == "Minimum average annual turnover of Rs. 5 crore"
    assert reqs[0]["threshold"]["original_value"] == "Rs. 5 crore"
    assert reqs[1]["normalized_description"] is None
    assert reqs[1]["needs_review"] is True
    assert "meaning cannot be established" in (reqs[1]["review_reason"] or "")


def test_review_count_matches_flags_and_status_partial():
    """P7: reported count equals actual flags; failed chunk -> PARTIAL + failed_chunks."""
    from app.agents.tender_analyst.nodes import assemble_final_result_node

    reqs = _norm([
        {"requirement_id": "R1", "category": "DEADLINES", "title": "Bid end",
         "original_text": "Bid ends 24-10-2026", "mandatory": True, "page": 1},
        {"requirement_id": "R1", "category": "OTHER", "title": "Vague note",
         "original_text": "Something unclear applies sometimes", "mandatory": None, "page": 2},
    ])
    state = {
        "tender_id": "t", "document_name": "d.pdf",
        "parsed_pages": [{"extraction_status": "SUCCESS"}] * 2,
        "warnings": [], "metadata": {"tender_title": "T"}, "requirements": reqs,
        "issues": [], "validation_errors": ["Chunk 5 extraction failed: boom"],
        "failed_chunks": [5], "retry_count": 0, "analysis_status": "PENDING",
        "final_result": None, "error_details": None,
    }
    out = assemble_final_result_node(state)
    final = out["final_result"]
    assert final["requirements_needing_review"] == sum(1 for r in final["requirements"] if r["needs_review"])
    assert final["analysis_status"] == "PARTIAL"
    assert final["failed_chunks"] == [5]
    ids = [r["requirement_id"] for r in final["requirements"]]
    assert len(ids) == len(set(ids))


def test_partial_results_preserved_on_chunk_failure(monkeypatch):
    """P7: one chunk raising must not wipe other chunks' requirements."""
    from app.agents.tender_analyst import nodes as nodes_mod
    from app.agents.tender_analyst.llm_schemas import LLMSimpleRequirementList

    def fake_invoke(schema, prompt, operation="", fallback_schemas=()):
        if "Page 2" in prompt:
            raise RuntimeError("boom")
        return LLMSimpleRequirementList(requirements=[{
            "requirement_id": "R1", "category": "DEADLINES", "title": "Bid end",
            "original_text": "Bid ends 24-10-2026", "mandatory": True, "page": 1,
        }])

    monkeypatch.setattr(nodes_mod, "get_llm", lambda: MagicMock())
    monkeypatch.setattr(nodes_mod, "_invoke_structured", fake_invoke)
    state = {
        "tender_id": "t", "document_name": "d.pdf",
        "parsed_pages": [
            {"page_number": 1, "text": "Bid ends 24-10-2026", "extraction_status": "SUCCESS"},
            {"page_number": 2, "text": "Turnover clause", "extraction_status": "SUCCESS"},
        ],
        "warnings": [], "metadata": {}, "requirements": [], "issues": [],
        "validation_errors": [], "failed_chunks": [], "retry_count": 0,
        "analysis_status": "PENDING", "final_result": None, "error_details": None,
    }
    out = nodes_mod.extract_requirements_by_chunk_node(state)
    assert len(out["requirements"]) == 1
    assert out["failed_chunks"] == [1]
    assert any("Chunk 1" in e for e in out["validation_errors"])


def test_deadline_only_is_not_conditional():
    """P6: a bare deadline in applicability must not trigger a conditional flag."""
    reqs = _norm([{
        "requirement_id": "R1", "category": "DEADLINES",
        "title": "Bid opening date",
        "original_text": "Bid opens on 30-05-2025 15:30:00",
        "mandatory": True, "deadline": "30-05-2025 15:30:00", "page": 1,
    }])
    assert "Deadlines:" in (reqs[0]["applicability_condition"] or "")
    assert "Conditional requirement" not in (reqs[0]["review_reason"] or "")


def test_deadline_only_is_not_conditional():
    """P6: a bare deadline in applicability must not trigger a conditional flag."""
    reqs = _norm([{
        "requirement_id": "R1", "category": "DEADLINES",
        "title": "Bid opening date",
        "original_text": "Bid opens on 30-05-2025 15:30:00",
        "mandatory": True, "deadline": "30-05-2025 15:30:00", "page": 1,
    }])
    assert "Deadlines:" in (reqs[0]["applicability_condition"] or "")
    assert "Conditional requirement" not in (reqs[0]["review_reason"] or "")


def test_estimated_bid_value_not_mistaken_for_turnover():
    """P1-contradiction: different financial concepts, same category+unit,
    must NOT be flagged — regression test for live ISSUE-007 false positive."""
    from app.agents.tender_analyst.issues import detect_issues

    reqs = _norm([
        {"requirement_id": "R4", "category": "FINANCIAL_ELIGIBILITY",
         "title": "Minimum Turnover",
         "original_text": "Minimum Average Annual Turnover of the bidder (For 3 Years) 1156.37 Lakh (s)",
         "mandatory": True, "threshold_text": "minimum 115637000 INR", "page": 1},
        {"requirement_id": "R7-2", "category": "FINANCIAL_ELIGIBILITY",
         "title": "Estimated Bid Value",
         "original_text": "Estimated Bid Value in INR (Inclusive of all taxes) 493713737.6",
         "mandatory": None, "threshold_text": "493713737.6 INR", "page": 1},
    ])
    issues = detect_issues(reqs, {}, "d.pdf")
    contras = [i for i in issues if i["issue_type"] == "CONTRADICTION"]
    assert contras == []
    # ...while a genuine same-measure conflict still fires.
    reqs2 = _norm([
        {"requirement_id": "R1", "category": "FINANCIAL_ELIGIBILITY",
         "title": "Minimum turnover",
         "original_text": "Minimum turnover of 100 lakh",
         "mandatory": True, "threshold_text": "minimum 100 lakh", "page": 1},
        {"requirement_id": "R2", "category": "FINANCIAL_ELIGIBILITY",
         "title": "Bidder turnover requirement",
         "original_text": "Bidder should have turnover of 50 lakh",
         "mandatory": True, "threshold_text": "minimum 50 lakh", "page": 2},
    ])
    issues2 = detect_issues(reqs2, {}, "d.pdf")
    contras2 = [i for i in issues2 if i["issue_type"] == "CONTRADICTION"]
    assert len(contras2) == 1
    assert {"R1", "R2"} == set(contras2[0]["affected_requirement_ids"])
    assert "RequirementCategory" not in contras2[0]["description"]
    assert contras2[0]["source_references"]


def test_partial_exemption_links_base_and_stays_partial():
    """P2-exemption: relief reframed (no obligation threshold), linked to the
    base requirement, partial never upgraded to full, scope flagged."""
    from app.agents.tender_analyst.llm_schemas import link_exemptions

    reqs = _norm([
        {"requirement_id": "R4", "category": "FINANCIAL_ELIGIBILITY",
         "title": "Minimum Turnover",
         "original_text": "Minimum Average Annual Turnover 1156.37 Lakh",
         "mandatory": True, "threshold_text": "minimum 115637000 INR", "page": 1},
        {"requirement_id": "R7", "category": "FINANCIAL_ELIGIBILITY",
         "title": "MSE Turnover Relaxation",
         "original_text": "MSE Relaxation for Turnover Yes | Partial | Turn over value - 1156.37 (in lakhs)",
         "mandatory": None, "condition": "Applicable to MSE bidders",
         "threshold_text": "partial up to 115637000 INR",
         "evidence": "MSE certification", "page": 1},
    ])
    linked = link_exemptions(reqs)
    base = next(r for r in linked if r["requirement_id"] == "R4")
    ex = next(r for r in linked if r["requirement_id"] == "R7")
    assert base["threshold"]["operator"] == ">="
    assert base["threshold"]["value"] == 115637000.0
    assert ex["threshold"]["value"] is None
    assert ex["threshold"]["operator"] == "unknown"
    assert ex["depends_on"] == ["R4"]
    assert "partial relief from R4" in (ex["normalized_description"] or "")
    assert "full" not in (ex["normalized_description"] or "").lower()
    assert ex["needs_review"] is True
    assert "Partial exemption scope" in (ex["review_reason"] or "")


def test_missing_attachment_dedupes_generic_phrases():
    """P3: one issue per missing document; generic phrases fold into the
    specific document on the same requirement; independent docs stay split."""
    from app.agents.tender_analyst.issues import detect_issues

    reqs = _norm([
        {"requirement_id": "R1", "category": "MANDATORY_DOCUMENTS",
         "title": "Buyer Added Bid Specific SLA",
         "original_text": "Buyer Added Bid Specific SLA File Attachment Click here to view the file. Upload SLA document.",
         "mandatory": True, "evidence": "SLA document", "page": 6},
        {"requirement_id": "R2", "category": "MANDATORY_DOCUMENTS",
         "title": "Corrigendum note",
         "original_text": "Refer corrigendum 2 for revised dates.",
         "mandatory": True, "page": 2},
        {"requirement_id": "R3", "category": "MANDATORY_DOCUMENTS",
         "title": "ATC upload",
         "original_text": "Buyer uploaded ATC document Click here to view the file.",
         "mandatory": True, "evidence": "ATC document", "page": 6},
    ])
    issues = detect_issues(reqs, {}, "d.pdf")
    missing = [i for i in issues if i["issue_type"] == "MISSING_ATTACHMENT"]
    labels = sorted(" ".join(
        w for w in i["description"].split("'")[1:2]
    ) for i in missing)
    assert len(missing) == 3, [i["description"] for i in missing]
    joined = " | ".join(i["description"] for i in missing)
    assert "SLA document" in joined and "ATC document" in joined and "corrigendum" in joined
    assert "linked file" not in joined and "file attachment" not in joined.replace("File Attachment", "")
    sla = next(i for i in missing if "SLA document" in i["description"])
    assert sla["affected_requirement_ids"] == ["R1"]


def test_semantically_accurate_descriptions():
    """P4: deadlines read as constraints/dates, informational figures as
    reference data, prohibitions keep negation, bare titles gain detail."""
    reqs = _norm([
        {"requirement_id": "R1", "category": "DEADLINES",
         "title": "Bid End Date",
         "original_text": "Bid ends on 24-10-2026 16:00:00",
         "mandatory": True, "deadline": "24-10-2026 16:00:00", "page": 1},
        {"requirement_id": "R2", "category": "FINANCIAL_ELIGIBILITY",
         "title": "Estimated Bid Value",
         "original_text": "Estimated Bid Value in INR (Inclusive of all taxes) 493713737.6",
         "mandatory": None, "threshold_text": "493713737.6 INR", "page": 1},
        {"requirement_id": "R3", "category": "LEGAL_AND_STATUTORY",
         "title": "Malicious Code Certificate",
         "original_text": "The hardware must not contain embedded malicious code.",
         "mandatory": True, "page": 6},
        {"requirement_id": "R4", "category": "MANDATORY_DOCUMENTS",
         "title": "Final Project Plan",
         "original_text": "Bidder must submit the Final Project Plan within 30 days of award.",
         "mandatory": True, "page": 6},
    ])
    by_id = {r["requirement_id"]: r for r in reqs}
    assert by_id["R1"]["normalized_description"].startswith("Submission constraint:")
    assert "24-10-2026" in by_id["R1"]["normalized_description"]
    assert "Bidder must" not in by_id["R1"]["normalized_description"]
    assert by_id["R2"]["normalized_description"].startswith("Reference figure:")
    assert "Threshold:" not in by_id["R2"]["normalized_description"]
    assert "must not" in by_id["R3"]["normalized_description"].lower()
    assert by_id["R4"]["normalized_description"] != "Bidder must: Final Project Plan."
    assert "30 days" in by_id["R4"]["normalized_description"]


def test_metadata_title_targeted_retry(monkeypatch):
    """P5: null tender_title triggers one focused retry; result merged + noted."""
    from app.agents.tender_analyst import nodes as nodes_mod
    from app.agents.tender_analyst.llm_schemas import LLMMetadata

    calls = {"n": 0}

    def fake_invoke(schema, prompt, operation="", fallback_schemas=()):
        calls["n"] += 1
        if operation == "extract_metadata_title_retry":
            return LLMMetadata(tender_title="Hiring of Agency for IT Projects", tender_reference="GEM/2026/B/8111208")
        return LLMMetadata(tender_title=None, tender_reference="GEM/2026/B/8111208")

    monkeypatch.setattr(nodes_mod, "_invoke_structured", fake_invoke)
    state = {
        "tender_id": "t", "document_name": "d.pdf",
        "parsed_pages": [{"page_number": 1, "text": "Some tender content", "extraction_status": "SUCCESS"}],
        "warnings": [], "metadata": {}, "requirements": [], "issues": [],
        "validation_errors": [], "failed_chunks": [], "retry_count": 0,
        "analysis_status": "PENDING", "final_result": None, "error_details": None,
    }
    out = nodes_mod.extract_metadata_node(state)
    assert out["metadata"]["tender_title"] == "Hiring of Agency for IT Projects"
    assert out["metadata"]["tender_reference"] == "GEM/2026/B/8111208"
    assert calls["n"] == 2
    assert any("tender_title" in w for w in out["warnings"])


def test_final_counts_match_output():
    """P7: requirement count, review count and unique IDs match final output."""
    from app.agents.tender_analyst.nodes import assemble_final_result_node

    reqs = _norm([
        {"requirement_id": "R1", "category": "DEADLINES", "title": "Bid end",
         "original_text": "Bid ends 24-10-2026", "mandatory": True, "page": 1},
        {"requirement_id": "R1", "category": "OTHER", "title": "Vague note",
         "original_text": "Something unclear applies sometimes", "mandatory": None, "page": 2},
    ])
    state = {
        "tender_id": "t", "document_name": "d.pdf",
        "parsed_pages": [{"extraction_status": "SUCCESS"}] * 2,
        "warnings": [], "metadata": {"tender_title": "T"}, "requirements": reqs,
        "issues": [{"issue_id": "ISSUE-001", "issue_type": "MISSING_ATTACHMENT",
                    "description": "x", "severity": "medium",
                    "affected_requirement_ids": ["R1"], "source_references": [],
                    "requires_human_review": True}],
        "validation_errors": [], "failed_chunks": [], "retry_count": 0,
        "analysis_status": "PENDING", "final_result": None, "error_details": None,
    }
    final = assemble_final_result_node(state)["final_result"]
    assert final["analysis_status"] == "SUCCESS"
    assert len(final["requirements"]) == 2
    flagged = [r for r in final["requirements"] if r["needs_review"]]
    assert final["requirements_needing_review"] == len(flagged) == 1
    ids = [r["requirement_id"] for r in final["requirements"]]
    assert len(ids) == len(set(ids))
    assert len(final["issues"]) == 1
    assert "completeness" in final
    assert final["completeness"]["requirements_flagged"] == 1


def test_litellm_overload_switches_to_groq(monkeypatch):
    """LiteLLM over-limit must abandon the primary chain and succeed via Groq."""
    from app.agents.tender_analyst import nodes as nodes_mod
    from app.agents.tender_analyst.llm_schemas import LLMSimpleRequirementList

    def boom_factory():
        raise RuntimeError("429 rate limit exceeded: over quota for claude-haiku")

    groq_hit = {"n": 0}

    class FakeStructured:
        def invoke(self, prompt):
            groq_hit["n"] += 1
            return LLMSimpleRequirementList(requirements=[{
                "requirement_id": "R1", "category": "DEADLINES", "title": "Bid end",
                "original_text": "Bid ends 24-10-2026", "mandatory": True, "page": 1,
            }])

    fake_groq = MagicMock()
    fake_groq.with_structured_output = MagicMock(return_value=FakeStructured())
    monkeypatch.setattr(nodes_mod, "get_llm", boom_factory)
    monkeypatch.setattr(nodes_mod, "get_groq_llm", lambda max_tokens=None: fake_groq)

    out = nodes_mod._invoke_structured(
        LLMSimpleRequirementList, "prompt", operation="test_provider_switch"
    )
    assert groq_hit["n"] == 1
    assert out.requirements[0].requirement_id == "R1"


def test_groq_used_directly_when_litellm_unconfigured(monkeypatch):
    """No LITELLM_API_KEY -> get_llm() builds the Groq client (backward compat)."""
    from app.agents.tender_analyst import nodes as nodes_mod
    from app.core import config as config_mod

    monkeypatch.setattr(config_mod.settings, "LITELLM_API_KEY", None)
    llm = nodes_mod.get_llm()
    assert type(llm).__name__ == "ChatGroq"
