"""Deterministic compliance rules (P3 — plain Python, no LLM).

Consumes the Tender Analyst's requirements and the Evidence Agent's mapping
and produces per-requirement compliance verdicts in exactly four states:

- COMPLIANT: evidence proves the requirement (numeric re-check passes, no
  expiry, no contradiction).
- NON_COMPLIANT: proven failure (missing mandatory evidence, figure
  mismatch, expired document). Always raises a blocker.
- PENDING: waiting on documents/info (non-mandatory gaps, exemption-covered
  bases). Resumable, never a silent pass.
- NEEDS_REVIEW: ambiguous — a human must decide. Escalates, never assumes.

The engine re-verifies numeric claims independently instead of trusting P2
statuses, and treats prohibitions / informational clauses / buyer
discretion by their semantics (absence of a violation is compliance, not
a gap).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.agents.evidence_agent.verification import _meets, canon_unit, coerce_computed
from app.agents.tender_analyst.issues import parse_date
from app.core.requirement_refs import build_ref_map, resolve_requirement_refs
from app.models.compliance import (
    Blocker,
    BlockerType,
    ComplianceMatrix,
    ComplianceStatus,
    RequirementCompliance,
)

_NO_ACTION_SEMANTICS = frozenset({"PROHIBITION", "INFORMATIONAL", "BUYER_DISCRETION"})

_EXPIRY_NEAR_RE = re.compile(
    r"(expir\w*|valid\s+(till|until|up\s*to|through|thru)|validity|"
    r"lapse[sd]?|renew\w*|effective\s+(till|until))",
    re.IGNORECASE,
)
_CANDIDATE_DATE_RE = re.compile(
    r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{4}-\d{1,2}-\d{1,2}"
)


def _sem(value: Any) -> str:
    if isinstance(value, str):
        return value.upper()
    return str(getattr(value, "value", value) or "").upper()


def _find_expiry(
    excerpts: list[str], deadline: datetime | None
) -> tuple[bool, str | None, str | None]:
    """Spot expired documents: an expiry-anchored date before the deadline.

    Returns (expired, date_text, snippet). Only explicit expiry wording
    counts — a bare "valid certificate" is not an expiry claim.
    """
    today = datetime.now()
    cutoff = deadline or today
    for text in excerpts:
        text = str(text or "")
        for match in _CANDIDATE_DATE_RE.finditer(text):
            window = text[max(0, match.start() - 60) : match.end() + 20]
            if not _EXPIRY_NEAR_RE.search(window):
                continue
            parsed = parse_date(match.group(0))
            if parsed is None:
                continue
            if parsed < cutoff:
                snippet = re.sub(r"\s+", " ", window.strip())[:160]
                return True, match.group(0), snippet
    return False, None, None


def _numeric_recheck(
    req: dict[str, Any], computed: Any, computed_unit: Any
) -> tuple[bool | None, str]:
    """Independent numeric re-verification. None = not applicable.

    Returns (passes, explanation). Uses the requirement's own threshold and
    the evidence's computed figure — never P2's status.
    """
    threshold = req.get("threshold") or {}
    required = threshold.get("value")
    operator = threshold.get("operator", "=")
    company = coerce_computed(computed)
    if required is None or operator == "unknown" or company is None:
        return None, ""
    if canon_unit(computed_unit) != canon_unit(threshold.get("unit", "")):
        return None, ""
    met = _meets(operator, company, required)
    if met is None:
        return None, ""
    from app.agents.evidence_agent.verification import fmt_number

    claim = (
        f"company {fmt_number(company)} {threshold.get('unit', '')} vs "
        f"required {operator} {fmt_number(required)} {threshold.get('unit', '')}"
    )
    return met, claim


def evaluate_compliance(
    analysis: dict[str, Any], evidence_map: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Full deterministic evaluation. Returns (compliance items, blockers)."""
    requirements = analysis.get("requirements", []) or []
    metadata = analysis.get("metadata", {}) or {}
    ev_by_id = {
        str(i.get("requirement_id", "?")): i
        for i in (evidence_map.get("items", []) or [])
    }
    deadline = None
    try:
        from app.agents.tender_analyst.issues import parse_date as _pd

        deadline = _pd((metadata or {}).get("submission_deadline"))
    except Exception:
        deadline = None

    interim: list[dict[str, Any]] = []
    for req in requirements:
        rid = str(req.get("requirement_id", "?"))
        title = str(req.get("title", "") or "")
        mandatory = req.get("mandatory")
        semantic = _sem(req.get("semantic_type"))
        ev = ev_by_id.get(rid, {})
        ev_status = str(ev.get("status", "missing") or "missing")
        excerpts = [
            str(h.get("excerpt", ""))
            for h in (ev.get("hits", []) or [])
            if h.get("excerpt")
        ]
        reasons: list[str] = []
        pending_blocker: dict[str, Any] | None = None

        if ev_status == "satisfied":
            ok, claim = _numeric_recheck(
                req, ev.get("computed_value"), ev.get("computed_unit")
            )
            if ok is False:
                status = ComplianceStatus.NON_COMPLIANT
                reasons.append(f"Independent re-check failed: {claim}.")
                pending_blocker = ("FAILED_CHECK", reasons[-1])
            else:
                status = ComplianceStatus.COMPLIANT
                reasons.append(
                    f"Evidence satisfies the requirement"
                    + (f" ({claim})" if claim else f" ({ev.get('notes', '') or 'documentary match'}).")
                )
        elif ev_status == "needs_review":
            status = ComplianceStatus.NEEDS_REVIEW
            reasons.append(
                f"Evidence ambiguous: {(ev.get('notes') or 'see evidence excerpts').strip()}."
            )
            if mandatory:
                pending_blocker = (
                    "UNRESOLVED_REVIEW",
                    f"Mandatory requirement {rid} needs human review: {reasons[-1]}",
                )
        elif ev_status == "partial":
            status = ComplianceStatus.PENDING
            reasons.append(
                f"Evidence incomplete: {(ev.get('notes') or 'gaps remain').strip()}."
            )
            if mandatory:
                pending_blocker = (
                    "INCOMPLETE_EVIDENCE",
                    f"Mandatory requirement {rid} only partially evidenced.",
                )
        else:  # missing / unmapped
            if semantic in _NO_ACTION_SEMANTICS:
                status = ComplianceStatus.COMPLIANT
                reasons.append(
                    "No contrary evidence and no bidder action required "
                    f"({semantic.lower().replace('_', ' ')})."
                )
            elif mandatory:
                status = ComplianceStatus.NON_COMPLIANT
                reasons.append("Mandatory requirement has no supporting evidence.")
                pending_blocker = (
                    "MISSING_DOCUMENT",
                    f"Mandatory requirement {rid} ('{title[:80]}') lacks evidence.",
                )
            else:
                status = ComplianceStatus.PENDING
                reasons.append("No supporting evidence found yet.")

        # Expiry gate on satisfied items: an expired certificate is not proof.
        expired, exp_date, snippet = (
            _find_expiry(excerpts, deadline) if status == ComplianceStatus.COMPLIANT else (False, None, None)
        )
        if expired:
            status = ComplianceStatus.NON_COMPLIANT
            reasons.append(
                f"Supporting document expired ({exp_date}: '{snippet}')."
            )
            pending_blocker = (
                "EXPIRED_DOCUMENT",
                f"Requirement {rid} relies on an expired document ({exp_date}).",
            )

        interim.append(
            {
                "requirement_id": rid,
                "requirement_title": title or None,
                "status": status,
                "reasons": reasons,
                "evidence_status": ev_status,
                "blocker": pending_blocker,
            }
        )

    # Exemption relief: a satisfied exemption linked to a failed base turns
    # the base into PENDING ("covered, confirm applicability") — never a
    # silent pass, never an auto-COMPLIANT.
    by_id = {r["requirement_id"]: r for r in interim}
    satisfied_exemptions = {
        r["requirement_id"]
        for r in requirements
        if _sem(r.get("semantic_type")) == "EXEMPTION"
        and str((ev_by_id.get(str(r.get('requirement_id', '?')), {}) or {}).get("status", "")) == "satisfied"
    }
    for req in requirements:
        rid = str(req.get("requirement_id", "?"))
        entry = by_id.get(rid)
        if not entry or entry["status"] != ComplianceStatus.NON_COMPLIANT:
            continue
        linked = [
            d for d in (req.get("depends_on") or []) if str(d) in satisfied_exemptions
        ]
        if linked:
            entry["status"] = ComplianceStatus.PENDING
            entry["reasons"].append(
                f"Covered by satisfied exemption(s) {', '.join(linked)} — "
                f"confirm applicability before proceeding."
            )
            entry["blocker"] = None

    items: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    n = 1

    def _add_blocker(
        btype: BlockerType, description: str, severity: str,
        rids: list[str], hint: str,
    ) -> str:
        nonlocal n
        bid = f"BLK-{n:03d}"
        n += 1
        blockers.append(
            Blocker(
                blocker_id=bid,
                blocker_type=btype,
                description=description,
                severity=severity,
                affected_requirement_ids=rids,
                resolution_hint=hint,
            ).model_dump()
        )
        return bid

    for entry in interim:
        blocker = entry.pop("blocker")
        if blocker is not None:
            btype_name, desc = blocker
            mandatory = next(
                (
                    bool(r.get("mandatory"))
                    for r in requirements
                    if str(r.get("requirement_id", "?")) == entry["requirement_id"]
                ),
                False,
            )
            bid = _add_blocker(
                BlockerType[btype_name],
                desc,
                "high" if mandatory else "medium",
                [entry["requirement_id"]],
                _resolution_hint(btype_name, entry["requirement_id"]),
            )
            entry["blocker_ids"] = [bid]
        else:
            entry["blocker_ids"] = []
        items.append(RequirementCompliance(**entry).model_dump())

    # Tender-level contradictions always block: figures must be reconciled.
    for issue in analysis.get("issues", []) or []:
        if str(issue.get("issue_type", "")) != "CONTRADICTION":
            continue
        rids = [str(r) for r in (issue.get("affected_requirement_ids", []) or [])]
        bid = _add_blocker(
            BlockerType.CONTRADICTION,
            f"Tender contradiction ({issue.get('issue_id', '?')}): "
            f"{str(issue.get('description', ''))[:220]}",
            "high",
            rids,
            "Reconcile the conflicting tender clauses before proceeding.",
        )
        for item in items:
            if item["requirement_id"] in set(rids):
                item["blocker_ids"] = [*item["blocker_ids"], bid]

    # Human-readable citations: expand bare IDs in reasons, descriptions
    # and hints into title + description (structured ID fields untouched).
    ref_map = build_ref_map(requirements)
    for item in items:
        item["reasons"] = [
            resolve_requirement_refs(r, ref_map) for r in item["reasons"]
        ]
    for blocker in blockers:
        blocker["description"] = resolve_requirement_refs(
            blocker["description"], ref_map
        )
        blocker["resolution_hint"] = resolve_requirement_refs(
            blocker["resolution_hint"], ref_map
        )

    return items, blockers


def _resolution_hint(btype_name: str, rid: str) -> str:
    return {
        "MISSING_DOCUMENT": (
            f"Upload the supporting document for {rid} via evidence-index, "
            f"then resume the pipeline."
        ),
        "FAILED_CHECK": (
            f"Company figure does not meet {rid}'s threshold — supply "
            f"stronger evidence or concede the requirement."
        ),
        "EXPIRED_DOCUMENT": (
            f"Upload a renewed document covering {rid}, then resume."
        ),
        "INCOMPLETE_EVIDENCE": (
            f"Complete the evidence set for {rid} (see notes), then resume."
        ),
        "UNRESOLVED_REVIEW": (
            f"Resolve the human-review flag on {rid}, then resume."
        ),
    }.get(btype_name, f"Resolve the finding for {rid}, then resume.")


def build_matrix(
    tender_id: str,
    items: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
) -> ComplianceMatrix:
    summary = {
        "compliant": sum(1 for i in items if i["status"] == ComplianceStatus.COMPLIANT),
        "non_compliant": sum(
            1 for i in items if i["status"] == ComplianceStatus.NON_COMPLIANT
        ),
        "pending": sum(1 for i in items if i["status"] == ComplianceStatus.PENDING),
        "needs_review": sum(
            1 for i in items if i["status"] == ComplianceStatus.NEEDS_REVIEW
        ),
        "blockers": len(blockers),
    }
    return ComplianceMatrix(
        tender_id=tender_id,
        items=[RequirementCompliance(**i) for i in items],
        blockers=[Blocker(**b) for b in blockers],
        summary=summary,
        decision="PAUSE" if blockers else "PROCEED",
    )
