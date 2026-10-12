"""Repeatable evaluation (P6 seed): fixtures mode needs no LLM/quota.

    uv run python scripts/eval_run.py --fixtures   # deterministic checks
    uv run python scripts/eval_run.py --live tender.pdf [--profile sample]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _req(rid, title, mandatory=True, threshold=None, semantic=None):
    return {
        "requirement_id": rid, "title": title, "original_text": title,
        "category": "OTHER", "semantic_type": semantic, "mandatory": mandatory,
        "applicability_condition": None, "threshold": threshold,
        "required_evidence": [], "source_references": [],
        "depends_on": [], "confidence": None, "needs_review": False,
        "review_reason": None, "normalized_description": title,
    }


def _th(op, value, unit="INR"):
    return {"operator": op, "value": value, "unit": unit,
            "original_value": f"{op} {value} {unit}"}


def _ev(rid, status, computed=None, unit=None, notes="n", hits=()):
    return {
        "requirement_id": rid, "requirement_title": rid, "status": status,
        "confidence": 0.8, "hits": list(hits),
        "computed_value": computed, "computed_unit": unit, "notes": notes,
    }


def _hit(doc="D.pdf", excerpt="x"):
    return {"document_name": doc, "page_number": 1, "excerpt": excerpt,
            "score": 0.8, "supports": "supports"}


def run_fixtures():
    from app.agents.compliance_agent.rules import evaluate_compliance
    from app.agents.verification_agent.rules import verify_package

    checks = []
    passed = []

    def check(name, cond, detail=""):
        checks.append(name)
        if cond:
            passed.append(name)
        else:
            print(f"FAIL: {name} {detail}")

    analysis = {
        "requirements": [
            _req("R1", "Turnover", True, _th(">=", 100.0), "ELIGIBILITY_CONDITION"),
            _req("R2", "Upload cert", True),
            _req("R3", "Opening date", False),
        ],
        "metadata": {},
        "issues": [],
    }
    evidence = {"items": [
        _ev("R1", "satisfied", 120.0, "INR", "meets floor", [_hit()]),
        _ev("R2", "missing", notes="absent"),
        _ev("R3", "missing", notes="absent"),
    ]}
    items, blockers = evaluate_compliance(analysis, evidence)
    by_id = {i["requirement_id"]: i for i in items}
    check("numeric match compliant", by_id["R1"]["status"] == "COMPLIANT")
    check("missing mandatory blocks", by_id["R2"]["status"] == "NON_COMPLIANT"
          and len(blockers) == 1)
    check("informational-ish missing pends", by_id["R3"]["status"] == "PENDING")

    drafts = [{
        "draft_id": "D-R1", "requirement_id": "R1", "kind": "requirement_response",
        "title": "Turnover", "body": "We meet turnover with 120 INR.",
        "evidence_refs": [{"document_name": "D.pdf", "page_number": 1}],
        "claimed_value": 120.0, "claimed_unit": "INR",
        "needs_human_signoff": False,
    }]
    report = verify_package(
        analysis["requirements"], evidence["items"], drafts, ["blocker BLK-001 noted"],
        blockers, [{"requirement_id": "R1", "status": "COMPLIANT"}],
    )
    check("clean package verifies", report.passed is True)

    bad = verify_package(
        analysis["requirements"], evidence["items"],
        [{**drafts[0], "claimed_value": 50.0}], ["x"], blockers,
        [{"requirement_id": "R1", "status": "COMPLIANT"}],
    )
    check("tampered figure fails verification",
          bad.passed is False and any(f.check == "numeric-claim" for f in bad.findings))

    print(f"fixtures: {len(passed)}/{len(checks)} passed")
    return 0 if len(passed) == len(checks) else 1


def run_live(pdf, profile):
    from app.orchestrator.service import OrchestratorService

    out = OrchestratorService.run_tender(pdf, Path(pdf).name, profile, True)
    comp = (out.get("compliance") or {})
    ver = (out.get("verification") or {})
    pkg = (out.get("package") or {})
    print(json.dumps({
        "tender_id": out.get("tender_id"),
        "pipeline_status": out.get("pipeline_status"),
        "requirements": len((out.get("analysis") or {}).get("requirements", [])),
        "compliance_summary": comp.get("summary"),
        "decision": comp.get("decision"),
        "drafts": len(pkg.get("drafts", [])),
        "verification_passed": ver.get("passed"),
        "findings": len(ver.get("findings", [])),
        "checkpoint": out.get("checkpoint_path"),
    }, indent=1))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", action="store_true")
    parser.add_argument("--live", default=None)
    parser.add_argument("--profile", default="sample")
    args = parser.parse_args()
    if args.live:
        raise SystemExit(run_live(args.live, args.profile))
    raise SystemExit(run_fixtures())
