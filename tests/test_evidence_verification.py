"""Tests for deterministic numeric verification of evidence (P2 engine).

The engine reads company figures from excerpts, normalizes units, and
applies the requirement's threshold operator. Pure functions — no network.
"""

from app.agents.evidence_agent import verification as V


def _req(rid="R1", title="Minimum turnover", threshold=None, semantic=None):
    return {
        "requirement_id": rid,
        "title": title,
        "normalized_description": title,
        "threshold": threshold,
        "semantic_type": semantic,
        "mandatory": True,
    }


def _th(op, value, unit, original=""):
    return {"operator": op, "value": value, "unit": unit, "original_value": original}


def _hit(doc="Turnover-Cert.pdf", page=1, excerpt=""):
    return {"document_name": doc, "page_number": page, "excerpt": excerpt, "score": 0.8}


def test_turnover_met():
    """The user's example: 10L required, company states 12L -> satisfied."""
    req = _req(threshold=_th(">=", 1000000.0, "INR", "Rs. 10 lakhs"))
    hits = [_hit(excerpt="Certified annual turnover of the company is Rs. 12 lakh for FY 2024-25.")]
    out = V.verify_numeric_requirement(req, hits)
    assert out is not None
    assert out["status"] == "satisfied"
    assert out["computed_value"] == 1200000.0
    assert "1,200,000" in out["notes"] and "1,000,000" in out["notes"]


def test_turnover_below_floor_is_missing_not_partial():
    req = _req(threshold=_th(">=", 115637000.0, "INR", "minimum 115637000 INR"))
    hits = [_hit(excerpt="Annual turnover Rs. 5 crore as per audited statements.")]
    out = V.verify_numeric_requirement(req, hits)
    assert out is not None
    assert out["status"] == "missing"  # definitive non-meet, not a gap
    assert out["computed_value"] == 50000000.0
    assert "does NOT meet" in out["notes"]


def test_lakh_scaling_exact_match():
    req = _req(threshold=_th(">=", 115637000.0, "INR", "minimum 115637000 INR"))
    hits = [_hit(excerpt="Average annual turnover 1156.37 Lakh certified.")]
    out = V.verify_numeric_requirement(req, hits)
    assert out is not None
    assert out["status"] == "satisfied"
    assert out["computed_value"] == 115637000.0


def test_dates_ignored_in_favour_of_figures():
    req = _req(
        title="Bid offer validity",
        threshold=_th(">=", 180.0, "Days", "180 days"),
    )
    hits = [_hit(excerpt="Bid valid till 24-10-2026. Offer validity 180 days confirmed.")]
    out = V.verify_numeric_requirement(req, hits)
    assert out is not None
    assert out["status"] == "satisfied"
    assert out["computed_value"] == 180.0


def test_unit_mismatch_abstains():
    req = _req(threshold=_th(">=", 45.0, "days", "within 45 days"))
    hits = [_hit(excerpt="EMD amount Rs. 9874275 deposited.")]
    assert V.verify_numeric_requirement(req, hits) is None


def test_max_operator():
    req = _req(
        title="Delivery period",
        threshold=_th("<=", 45.0, "days", "within 45 days"),
    )
    ok = V.verify_numeric_requirement(
        req, [_hit(excerpt="Delivery completed in 30 days.")]
    )
    assert ok is not None and ok["status"] == "satisfied"
    bad = V.verify_numeric_requirement(
        req, [_hit(excerpt="Delivery completed in 60 days.")]
    )
    assert bad is not None and bad["status"] == "missing"


def test_unknown_or_missing_threshold_abstains():
    req_none = _req(threshold=None)
    assert V.verify_numeric_requirement(req_none, [_hit(excerpt="turnover 5 crore")]) is None
    req_unk = _req(threshold=_th("unknown", None, "", "partial relaxation"))
    assert V.verify_numeric_requirement(req_unk, [_hit(excerpt="turnover 5 crore")]) is None


def test_informational_and_discretion_skipped():
    req = _req(
        title="Estimated Bid Value",
        threshold=_th("=", 493713737.6, "INR", "493713737.6 INR"),
        semantic="INFORMATIONAL",
    )
    hits = [_hit(excerpt="Our turnover is Rs. 500 crore.")]
    assert V.verify_numeric_requirement(req, hits) is None


def test_audit_downgrades_false_satisfied():
    """LLM claims satisfied but its own figure fails the operator test."""
    req = _req(threshold=_th(">=", 115637000.0, "INR", "minimum 115637000 INR"))
    status, adj, note = V.audit_numeric_claim(req, "satisfied", "50 lakh", "looks fine")
    assert status == "needs_review"
    assert adj == 0.4
    assert "does not meet" in note


def test_audit_leaves_consistent_claims_alone():
    req = _req(threshold=_th(">=", 115637000.0, "INR", "minimum 115637000 INR"))
    status, adj, note = V.audit_numeric_claim(req, "satisfied", "120000000", "meets it")
    assert (status, adj, note) == ("satisfied", None, None)


def test_coerce_computed_parses_verbatim_figures():
    assert V.coerce_computed("5 crore") == 50000000.0
    assert V.coerce_computed("1156.37 Lakh") == 115637000.0
    assert V.coerce_computed(42) == 42.0
    assert V.coerce_computed(None) is None
    assert V.coerce_computed("as applicable") is None


def test_normalize_mapping_parses_verbatim_computed():
    from app.agents.evidence_agent.llm_schemas import normalize_mapping

    out = normalize_mapping(
        {"status": "satisfied", "confidence": 0.9, "computed_value": "12 lakh",
         "computed_unit": "lakh", "notes": "ok"},
        "R1",
        [_hit(excerpt="turnover 12 lakh")],
        "Turnover",
        requirement=_req(threshold=_th(">=", 1000000.0, "INR", "Rs. 10 lakhs")),
    )
    assert out["computed_value"] == 1200000.0
    assert out["status"] == "satisfied"


def test_map_node_prefers_deterministic_verdict(monkeypatch):
    """map_evidence_node must use the engine verdict without calling the LLM."""
    from app.agents.evidence_agent import nodes as N

    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("LLM should not be called for decidable numerics")

    monkeypatch.setattr(N, "_invoke_structured", boom)
    state = {
        "tender_id": "t",
        "company_profile_id": "sample",
        "requirements": [
            {
                "requirement_id": "R1",
                "title": "Minimum turnover",
                "normalized_description": "Minimum turnover",
                "threshold": _th(">=", 1000000.0, "INR", "Rs. 10 lakhs"),
                "mandatory": True,
            }
        ],
        "retrieved": {
            "R1": [_hit(excerpt="Annual turnover Rs. 12 lakh certified.")]
        },
        "items": [],
        "unmapped": [],
        "warnings": [],
        "final_result": None,
    }
    out = N.map_evidence_node(state)
    assert calls["n"] == 0
    assert len(out["items"]) == 1
    assert out["items"][0]["status"] == "satisfied"
    assert out["unmapped"] == []


def test_verification_covers_all_measures():
    """Engine is measure-agnostic: floors, exact amounts, caps, percents,
    counts — plus honest abstention on word-form numbers and exemptions."""
    cases = [
        ("EMD exact", _req("E1", "EMD deposit",
                           {"operator": "=", "value": 9874275.0, "unit": "INR", "original_value": "Rs. 9874275"}),
         "EMD of Rs. 9874275 deposited via RTGS.", "satisfied", 9874275.0),
        ("delivery cap", _req("D1", "Delivery period",
                              {"operator": "<=", "value": 365.0, "unit": "days", "original_value": "within 365 days"}),
         "Delivery schedule of 300 days quoted.", "satisfied", 300.0),
        ("percent", _req("P1", "ePBG percent",
                         {"operator": "=", "value": 5.0, "unit": "percent", "original_value": "5 percent"}),
         "ePBG at 5 percent of contract value.", "satisfied", 5.0),
        ("count", _req("C1", "Bid count",
                       {"operator": "=", "value": 2.0, "unit": "bids", "original_value": "2 bids"}),
         "Minimum 2 bids received.", "satisfied", 2.0),
    ]
    for label, req, excerpt, status, value in cases:
        out = V.verify_numeric_requirement(
            req, [{"document_name": "D.pdf", "page_number": 1, "excerpt": excerpt, "score": 0.8}]
        )
        assert out is not None, label
        assert out["status"] == status, label
        assert out["computed_value"] == value, label
    # Word-form numbers ("seven years") have no digits to compare.
    out = V.verify_numeric_requirement(
        _req("E1", "Past experience",
             {"operator": ">=", "value": 7.0, "unit": "years", "original_value": "seven years"}),
        [{"document_name": "D.pdf", "page_number": 1,
          "excerpt": "Seven years of experience in bus hiring.", "score": 0.8}],
    )
    assert out is None
    # Exemptions are relief, never obligations — engine abstains even fenced.
    out = V.verify_numeric_requirement(
        _req("X1", "MSE relief",
             {"operator": ">=", "value": 115637000.0, "unit": "INR", "original_value": "x"},
             semantic="EXEMPTION"),
        [{"document_name": "D.pdf", "page_number": 1,
          "excerpt": "Turnover Rs. 200 crore.", "score": 0.8}],
    )
    assert out is None


def test_unanchored_figure_abstains():
    """Live R3-2/R10-2 false positives: a project plan's '7 days' must not
    verify the tender's auto-extension parameter or payment term."""
    auto_ext = {
        "requirement_id": "R3-2", "title": "Auto-extension period",
        "normalized_description": "Auto-extension period",
        "threshold": {"operator": "=", "value": 7.0, "unit": "days",
                      "original_value": "7 days"},
        "semantic_type": "CONTRACTUAL_TERM", "mandatory": None,
    }
    plan_hit = {
        "document_name": "25_Project_Implementation_Plan.pdf", "page_number": 1,
        "excerpt": "Project Implementation Plan with Milestones and Timelines. "
                   "Phase 1 completion in 7 days.",
        "score": 0.315,
    }
    assert V.verify_numeric_requirement(auto_ext, [plan_hit]) is None
    payment = dict(
        auto_ext, requirement_id="R10-2", title="Payment timeline",
        normalized_description="Payment timeline",
        threshold={"operator": "<=", "value": 45.0, "unit": "days",
                   "original_value": "within 45 days"},
    )
    assert V.verify_numeric_requirement(payment, [plan_hit]) is None


def test_computed_nulled_without_numeric_target():
    """Exemption mappings must not launder junk IDs into computed fields."""
    from app.agents.evidence_agent.llm_schemas import normalize_mapping

    out = normalize_mapping(
        {"status": "satisfied", "confidence": 0.88,
         "computed_value": "123456", "computed_unit": "DPIIT Recognition Number",
         "notes": "holds DPIIT certificate"},
        "R7-3",
        [{"document_name": "D.pdf", "page_number": 1, "excerpt": "DPIIT 123456", "score": 0.5}],
        "DPIIT Startup Turnover Relaxation",
        requirement={"requirement_id": "R7-3", "threshold": None,
                     "semantic_type": "EXEMPTION"},
    )
    assert out["computed_value"] is None
    assert out["computed_unit"] is None
    assert out["status"] == "satisfied"  # eligibility finding itself stands


def test_confidence_reflects_retrieval_score():
    """A perfect reading off a 0.31-score hit is not a 0.9 verdict."""
    out = V.verify_numeric_requirement(
        _req("R1", "Minimum turnover",
             {"operator": ">=", "value": 1000000.0, "unit": "INR", "original_value": "x"}),
        [{"document_name": "D.pdf", "page_number": 1,
          "excerpt": "Annual turnover Rs. 12 lakh certified.", "score": 0.31}],
    )
    assert out is not None and out["status"] == "satisfied"
    assert out["confidence"] < 0.9
    assert out["confidence"] == round(min(0.9, 0.55 + 0.45 * 0.31), 2)


def _ev_hit(doc="D.pdf", page=1, excerpt="x", score=0.5):
    return {"document_name": doc, "page_number": page, "excerpt": excerpt, "score": score}


def test_query_variants_cover_angles():
    """One vocabulary miss must not hide a document: title, evidence and
    measure angles are all queried."""
    from app.agents.evidence_agent.nodes import _requirement_queries

    req = {
        "title": "Bid security deposit",
        "normalized_description": "Bid security deposit",
        "threshold": {"operator": "=", "value": 9874275.0, "unit": "INR",
                      "original_value": "Rs. 9874275"},
        "required_evidence": ["EMD certificate"],
    }
    queries = _requirement_queries(req)
    assert 2 <= len(queries) <= 3
    joined = " | ".join(queries)
    assert "EMD" in joined  # evidence-led angle
    assert "9874275" in joined  # measure-led angle


def test_merge_hits_dedupes_pages():
    from app.agents.evidence_agent.nodes import _merge_hits

    merged = _merge_hits([
        [_ev_hit("A.pdf", 1, "x", 0.4)],
        [_ev_hit("A.pdf", 1, "x", 0.6), _ev_hit("B.pdf", 2, "y", 0.5)],
    ])
    assert [(h["document_name"], h["page_number"]) for h in merged] == [
        ("A.pdf", 1), ("B.pdf", 2)]
    assert merged[0]["score"] == 0.6  # best score kept
    assert merged[1]["score"] == 0.5  # score-descending order


def test_select_documents_prefers_topic_named_file():
    """A file literally named for the topic outranks a higher-count rival."""
    from app.agents.evidence_agent.nodes import _select_documents

    hits = [
        _ev_hit("Misc-Bundle.pdf", 1, "x", 0.55),
        _ev_hit("Misc-Bundle.pdf", 2, "x", 0.50),
        _ev_hit("Turnover-Cert.pdf", 1, "x", 0.52),
    ]
    top = _select_documents(hits, "Minimum turnover")
    assert top[0] == "Turnover-Cert.pdf"


def test_retrieve_node_enriches_from_selected_doc(monkeypatch):
    """Multi-query recall + doc-scoped enrichment + per-req cap."""
    from app.agents.evidence_agent import nodes as N

    seen_calls = []

    def fake_query(text, profile="sample", top_k=None, document_name=None):
        seen_calls.append((text[:30], top_k, document_name))
        if document_name == "Turnover-Cert.pdf":
            return [_ev_hit("Turnover-Cert.pdf", 2, "turnover continued 12 crore", 0.5)]
        if "EMD" in text or "turnover" in text.lower():
            return [_ev_hit("Turnover-Cert.pdf", 1, "annual turnover 12 crore", 0.6)]
        return []

    monkeypatch.setattr(N, "query_evidence", fake_query)
    monkeypatch.setattr(N.settings, "EVIDENCE_RETRIEVE_WIDTH", 8)
    monkeypatch.setattr(N.settings, "EVIDENCE_MAX_EXCERPTS", 6)
    state = {
        "tender_id": "t", "company_profile_id": "sample",
        "requirements": [{
            "requirement_id": "R1", "title": "Minimum turnover",
            "normalized_description": "Minimum turnover",
            "threshold": {"operator": ">=", "value": 1000000.0, "unit": "INR",
                          "original_value": "Rs. 10 lakhs"},
            "required_evidence": [], "mandatory": True,
        }],
        "retrieved": {}, "warnings": [],
    }
    out = N.retrieve_evidence_node(state)
    got = out["retrieved"]["R1"]
    assert {h["page_number"] for h in got} == {1, 2}  # enriched p.2 merged
    assert any(c[2] == "Turnover-Cert.pdf" for c in seen_calls)  # where-filter used
    assert len(seen_calls) >= 3  # multiple query angles issued


def test_verdict_without_hits_is_void():
    """satisfied/partial with zero excerpts collapse to missing + reason."""
    from app.agents.evidence_agent.llm_schemas import normalize_mapping

    out = normalize_mapping(
        {"status": "satisfied", "confidence": 0.9, "notes": "trust me"},
        "R1", [], "Title",
        requirement={"requirement_id": "R1", "threshold": None},
    )
    assert out["status"] == "missing"
    assert out["notes"]


def test_every_status_carries_a_reason():
    """No silent statuses: notes defaulted per outcome when model gives none."""
    from app.agents.evidence_agent.llm_schemas import normalize_mapping

    hits = [_ev_hit()]
    for status in ("satisfied", "partial", "missing", "needs_review", "bogus"):
        out = normalize_mapping({}, "R1", hits if status != "missing" else [],
                                "Title")
        assert out["notes"] and out["notes"].strip(), status
    out = normalize_mapping({}, "R1", [], "Title")
    assert out["status"] == "missing" and "No supporting excerpt" in out["notes"]
