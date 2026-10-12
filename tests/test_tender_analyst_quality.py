"""P1 normalizer quality tests: informational details vs real document asks."""


def _norm(item):
    from app.agents.tender_analyst.llm_schemas import normalize_requirement as N

    return N(item, "d.pdf", 1)


def test_informational_dates_never_demand_documents():
    """Bid dates/details are information, not document asks: model-invented
    evidence demands on them are cleared, real doc asks untouched."""
    date_req = _norm(
        {"requirement_id": "R1", "title": "Bid End Date",
         "original_text": "Bid ends on 24-10-2026 16:00:00",
         "mandatory": True, "evidence": "Bid document",
         "deadline": "24-10-2026 16:00:00", "page": 1}
    )
    assert date_req["semantic_type"] == "INFORMATIONAL"
    assert date_req["required_evidence"] == []
    assert date_req["mandatory"] is True  # still binding, just not a doc ask

    doc_req = _norm(
        {"requirement_id": "R2", "title": "Upload ATC certificate",
         "original_text": "Bidder must upload ATC certificate before deadline.",
         "mandatory": True, "evidence": "ATC certificate", "page": 1}
    )
    assert doc_req["required_evidence"] == ["ATC certificate"]

    th_req = _norm(
        {"requirement_id": "R3", "title": "Turnover",
         "original_text": "Minimum turnover Rs. 5 crore",
         "mandatory": True, "threshold_text": "Rs. 5 crore", "page": 1}
    )
    assert th_req["threshold"]["value"] == 50000000.0


def test_proof_free_details_dropped_from_list():
    """Tender details (dates, estimates) never become requirements: the list
    normalizer drops INFORMATIONAL clauses needing no bidder proof, while
    real document asks and measurable obligations are kept."""
    from app.agents.tender_analyst.llm_schemas import (
        LLMSimpleRequirementList,
        normalize_requirement_list,
    )

    data = LLMSimpleRequirementList(
        **{
            "requirements": [
                {"requirement_id": "R1", "title": "Bid End Date",
                 "original_text": "Bid ends on 24-10-2026 16:00:00",
                 "mandatory": True, "evidence": "Bid document",
                 "deadline": "24-10-2026 16:00:00", "page": 1},
                {"requirement_id": "R2", "title": "Estimated bid value",
                 "original_text": "Estimated bid value 1156.37 Lakh",
                 "mandatory": True, "page": 1},
                {"requirement_id": "R3", "title": "Minimum average annual turnover",
                 "original_text": "Bidder must have minimum average annual turnover "
                 "of Rs. 5 crore in last 3 years",
                 "mandatory": True, "threshold_text": "minimum Rs. 5 crore",
                 "evidence": "audited financial statements", "page": 1},
                {"requirement_id": "R4", "title": "Upload ATC certificate",
                 "original_text": "Bidder must upload ATC certificate before deadline.",
                 "mandatory": True, "evidence": "ATC certificate", "page": 1},
            ]
        }
    )
    out = normalize_requirement_list(data, "d.pdf", 1)
    assert [r["requirement_id"] for r in out] == ["R3", "R4"]
