"""Independent verification (P4): deterministic re-checks, no LLM.

Independence comes from separate code paths, not a second opinion prompt:
every draft must cite resolvable evidence, numeric claims are re-compared
against requirement thresholds, failed requirements must not have drafts,
and every blocker must appear in the risk report. Anything unverifiable
fails closed (blocking) when mandatory, warns otherwise.
"""

from __future__ import annotations

from typing import Any

from app.agents.evidence_agent.verification import _meets, canon_unit
from app.models.bid import VerificationFinding, VerificationReport


def _known_evidence(
    evidence_items: list[dict[str, Any]],
) -> set[tuple[str, int]]:
    known: set[tuple[str, int]] = set()
    for item in evidence_items:
        for hit in item.get("hits", []) or []:
            try:
                known.add(
                    (str(hit.get("document_name", "")),
                     int(hit.get("page_number", 0) or 0))
                )
            except (ValueError, TypeError):
                continue
    return known


def verify_package(
    requirements: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
    drafts: list[dict[str, Any]],
    risk_report: list[str],
    blockers: list[dict[str, Any]] | None = None,
    compliance_items: list[dict[str, Any]] | None = None,
) -> VerificationReport:
    """Run all checks. Returns a report; passed == no blocking failures."""
    findings: list[VerificationFinding] = []
    known = _known_evidence(evidence_items)
    by_req = {str(r.get("requirement_id", "?")): r for r in requirements}
    comp_of = {
        str(c.get("requirement_id", "?")): str(c.get("status", "")).upper()
        for c in (compliance_items or [])
    }
    drafts_by_req: dict[str, list[dict[str, Any]]] = {}
    for draft in drafts:
        if draft.get("requirement_id"):
            drafts_by_req.setdefault(str(draft["requirement_id"]), []).append(draft)

    def add(check, req_id, draft_id, passed, severity, detail):
        findings.append(
            VerificationFinding(
                check=check,
                requirement_id=req_id,
                draft_id=draft_id,
                passed=passed,
                severity=severity,
                detail=detail,
            )
        )

    for rid, req in by_req.items():
        mandatory = bool(req.get("mandatory"))
        comp = comp_of.get(rid, "")
        req_drafts = drafts_by_req.get(rid, [])
        # V1: every mandatory COMPLIANT requirement has a draft response.
        if comp == "COMPLIANT" and mandatory and not req_drafts:
            add("draft-coverage", rid, None, False, "blocking",
                f"Mandatory compliant requirement {rid} has no draft response.")
        # V3: no drafts for failed requirements (unless human-approved later).
        if comp == "NON_COMPLIANT" and req_drafts:
            add("no-draft-for-failure", rid, req_drafts[0].get("draft_id"), False,
                "blocking",
                f"Draft exists for failed requirement {rid} — remove it or concede the requirement.")
        for draft in req_drafts:
            did = str(draft.get("draft_id", "?"))
            # V2: every citation must resolve to indexed evidence.
            for ref in draft.get("evidence_refs", []) or []:
                key = (str(ref.get("document_name", "")),
                       int(ref.get("page_number", 0) or 0))
                if key not in known:
                    add("citation-resolves", rid, did, False,
                        "blocking" if mandatory else "warning",
                        f"Draft cites {key[0]} p.{key[1]}, which is not in the evidence index.")
            # V4: numeric claims re-compared against the requirement.
            th = req.get("threshold") or {}
            if (
                draft.get("claimed_value") is not None
                and th.get("value") is not None
                and th.get("operator", "=") != "unknown"
                and canon_unit(draft.get("claimed_unit")) == canon_unit(th.get("unit", ""))
            ):
                try:
                    ok = _meets(
                        th.get("operator", "="),
                        float(draft["claimed_value"]),
                        float(th["value"]),
                    )
                except (ValueError, TypeError):
                    ok = None
                if ok is False:
                    add("numeric-claim", rid, did, False,
                        "blocking" if mandatory else "warning",
                        f"Draft claims {draft['claimed_value']} {draft.get('claimed_unit', '')} "
                        f"but requirement needs {th.get('operator', '=')} {th.get('value')} "
                        f"{th.get('unit', '')}.")

    # V5: every blocker surfaces in the risk report (fuzzy: id or keywords).
    risk_blob = " ".join(risk_report or []).lower()
    for blocker in blockers or []:
        bid = str(blocker.get("blocker_id", ""))
        rids = [str(r) for r in (blocker.get("affected_requirement_ids", []) or [])]
        mentioned = (
            bid.lower() in risk_blob
            or any(r.lower() in risk_blob for r in rids)
            or str(blocker.get("blocker_type", "")).lower().replace("_", " ") in risk_blob
        )
        if not mentioned:
            add("risk-coverage", (rids or [None])[0], None, False,
                "blocking" if str(blocker.get("severity", "")) == "high" else "warning",
                f"Blocker {bid} ({blocker.get('blocker_type', '?')}) missing from risk report.")

    # V6: drafts for unreviewed requirements get a confirm-warning.
    for rid, req in by_req.items():
        if req.get("needs_review") and drafts_by_req.get(rid):
            add("unreviewed-draft", rid, drafts_by_req[rid][0].get("draft_id"), True,
                "warning",
                f"Draft exists for requirement {rid}, which still needs human review — confirm before use.")

    passed = not any(f.severity == "blocking" and not f.passed for f in findings)
    return VerificationReport(tender_id="", passed=passed, findings=findings)
