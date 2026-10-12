"""Tests for the P3 Compliance Agent (deterministic rules, no LLM)."""

from app.agents.compliance_agent.rules import build_matrix, evaluate_compliance
from app.models.compliance import ComplianceMatrix


def _req(rid, title="T", mandatory=True, semantic=None, threshold=None,
         depends_on=None):
    return {
        "requirement_id": rid,
        "title": title,
        "mandatory": mandatory,
        "semantic_type": semantic,
        "threshold": threshold,
        "required_evidence": [],
        "source_references": [],
        "depends_on": depends_on or [],
        "needs_review": False,
    }


def _th(op, value, unit="INR"):
    return {"operator": op, "value": value, "unit": unit,
            "original_value": f"{op} {value} {unit}"}


def _ev(rid, status, computed=None, unit=None, notes="n", hits=None):
    return {
        "requirement_id": rid,
        "requirement_title": rid,
        "status": status,
        "confidence": 0.8,
        "hits": hits or [],
        "computed_value": computed,
        "computed_unit": unit,
        "notes": notes,
    }


def _run(reqs, items, issues=None, metadata=None):
    analysis = {
        "requirements": reqs,
        "metadata": metadata or {},
        "issues": issues or [],
    }
    return evaluate_compliance(analysis, {"items": items})


def _hit(excerpt, doc="D.pdf"):
    return {"document_name": doc, "page_number": 1, "excerpt": excerpt,
            "score": 0.8, "supports": "supports"}


def test_satisfied_matching_numeric_is_compliant():
    items, blockers = _run(
        [_req("R1", "Turnover", True, "ELIGIBILITY_CONDITION",
              _th(">=", 115637000.0))],
        [_ev("R1", "satisfied", 120000000.0, "INR",
             hits=[_hit("turnover 120000000 INR")])],
    )
    assert items[0]["status"] == "COMPLIANT"
    assert blockers == []


def test_satisfied_mismatch_is_non_compliant_with_blocker():
    items, blockers = _run(
        [_req("R1", "Turnover", True, "ELIGIBILITY_CONDITION",
              _th(">=", 115637000.0))],
        [_ev("R1", "satisfied", 50000000.0, "INR",
             hits=[_hit("turnover 50000000 INR")])],
    )
    assert items[0]["status"] == "NON_COMPLIANT"
    assert len(blockers) == 1
    assert blockers[0]["blocker_type"] == "FAILED_CHECK"
    assert blockers[0]["severity"] == "high"
    assert blockers[0]["affected_requirement_ids"] == ["R1"]
    assert items[0]["blocker_ids"] == ["BLK-001"]


def test_missing_mandatory_blocks_missing_optional_pends():
    items, blockers = _run(
        [_req("R1", "Must upload", True), _req("R2", "Nice doc", False)],
        [_ev("R1", "missing"), _ev("R2", "missing")],
    )
    by_id = {i["requirement_id"]: i for i in items}
    assert by_id["R1"]["status"] == "NON_COMPLIANT"
    assert by_id["R2"]["status"] == "PENDING"
    assert len(blockers) == 1
    assert blockers[0]["blocker_type"] == "MISSING_DOCUMENT"
    assert blockers[0]["affected_requirement_ids"] == ["R1"]


def test_no_action_semantics_missing_is_compliant():
    items, blockers = _run(
        [
            _req("R1", "No malicious code", True, "PROHIBITION"),
            _req("R2", "Bid opening date", False, "INFORMATIONAL"),
            _req("R3", "Buyer may vary", None, "BUYER_DISCRETION"),
        ],
        [_ev("R1", "missing"), _ev("R2", "missing"), _ev("R3", "missing")],
    )
    assert all(i["status"] == "COMPLIANT" for i in items)
    assert blockers == []


def test_needs_review_escalates_mandatory_only():
    items, blockers = _run(
        [_req("R1", "Fuzzy", True), _req("R2", "Fuzzy2", False)],
        [_ev("R1", "needs_review", notes="unclear"),
         _ev("R2", "needs_review", notes="unclear")],
    )
    by_id = {i["requirement_id"]: i for i in items}
    assert by_id["R1"]["status"] == "NEEDS_REVIEW"
    assert by_id["R2"]["status"] == "NEEDS_REVIEW"
    assert len(blockers) == 1
    assert blockers[0]["blocker_type"] == "UNRESOLVED_REVIEW"
    assert blockers[0]["affected_requirement_ids"] == ["R1"]


def test_partial_mandatory_blocks_with_hint():
    items, blockers = _run(
        [_req("R1", "Docs", True)],
        [_ev("R1", "partial", notes="one of two certs found")],
    )
    assert items[0]["status"] == "PENDING"
    assert blockers[0]["blocker_type"] == "INCOMPLETE_EVIDENCE"
    assert "resume" in blockers[0]["resolution_hint"].lower()


def test_expired_document_blocks():
    items, blockers = _run(
        [_req("R1", "ISO cert", True)],
        [_ev("R1", "satisfied", notes="ISO 9001 certificate",
             hits=[_hit("ISO 9001:2015 certificate valid till 01-01-2020 issued.")])],
        metadata={"submission_deadline": "24-10-2026 16:00:00"},
    )
    assert items[0]["status"] == "NON_COMPLIANT"
    assert blockers[0]["blocker_type"] == "EXPIRED_DOCUMENT"


def test_satisfied_exemption_covers_failed_base_as_pending():
    """A satisfied exemption linked to a failed base turns the base PENDING
    (covered — confirm applicability), never a silent pass or hard fail."""
    reqs = [
        _req("R4", "Minimum turnover", True, "ELIGIBILITY_CONDITION",
             _th(">=", 115637000.0), depends_on=["R7"]),
        _req("R7", "MSE relaxation", None, "EXEMPTION"),
    ]
    items, blockers = _run(
        reqs,
        [_ev("R4", "missing"),
         _ev("R7", "satisfied", notes="MSE registered",
             hits=[_hit("Udyam registered")])],
    )
    by_id = {i["requirement_id"]: i for i in items}
    assert by_id["R4"]["status"] == "PENDING"
    assert "R7" in by_id["R4"]["reasons"][-1]
    assert by_id["R4"]["blocker_ids"] == []
    assert by_id["R7"]["status"] == "COMPLIANT"
    assert all(b["affected_requirement_ids"] != ["R4"] for b in blockers)


def test_contradiction_issue_becomes_blocker():
    items, blockers = _run(
        [_req("R1", "Turnover", True), _req("R2", "Turnover 2", True)],
        [_ev("R1", "satisfied", notes="ok", hits=[_hit("x")]),
         _ev("R2", "satisfied", notes="ok", hits=[_hit("y")])],
        issues=[{
            "issue_id": "ISSUE-007", "issue_type": "CONTRADICTION",
            "description": "Conflicting thresholds for turnover: R1 vs R2.",
            "severity": "high", "affected_requirement_ids": ["R1", "R2"],
            "source_references": [], "requires_human_review": True,
        }],
    )
    contra = [b for b in blockers if b["blocker_type"] == "CONTRADICTION"]
    assert len(contra) == 1
    assert set(contra[0]["affected_requirement_ids"]) == {"R1", "R2"}
    by_id = {i["requirement_id"]: i for i in items}
    assert contra[0]["blocker_id"] in by_id["R1"]["blocker_ids"]


def test_matrix_decision_and_summary():
    matrix = build_matrix("t", [], [])
    assert isinstance(matrix, ComplianceMatrix)
    assert matrix.decision == "PROCEED"
    assert matrix.summary == {
        "compliant": 0, "non_compliant": 0, "pending": 0,
        "needs_review": 0, "blockers": 0,
    }
    items, blockers = _run([_req("R1", "Must", True)], [_ev("R1", "missing")])
    matrix = build_matrix("t", items, blockers)
    assert matrix.decision == "PAUSE"
    assert matrix.summary["non_compliant"] == 1
    assert matrix.summary["blockers"] == 1
    assert matrix.blockers[0].blocker_id == "BLK-001"
