"""Deterministic post-extraction issue detection (P4).

Per-chunk extraction cannot see the whole tender, so cross-cutting findings
live here, computed over the merged requirement set plus metadata:

- MISSING_ATTACHMENT: buyer-side documents cited in the text (ATC, SLA,
  annexures, appendices, linked files) that were never supplied for analysis.
  Distinct from required_evidence, which lists documents the *bidder* must
  submit — those never raise issues.
- CONTRADICTION: conflicting mandatory flags, differing thresholds for the
  same topic, or deadline ordering violations, quoted with sources.

Every finding carries requirement IDs and copied source references so a
reviewer can verify it without re-reading the tender.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.agents.tender_analyst.llm_schemas import (
    find_attachment_refs,
    fmt_number,
    titles_equivalent,
    _enum_value,
)

# Labels identifying a SPECIFIC buyer document vs generic file phrases.
# Generic phrases ("linked file", "file attachment", ...) on a requirement
# that already names a specific document are folded into that document's
# issue instead of spawning duplicate issues.
SPECIFIC_ATTACHMENT_LABELS = frozenset({
    "ATC document",
    "SLA document",
    "annexure",
    "appendix",
    "addendum",
    "corrigendum",
    "amendment",
})

_DATE_FORMATS = (
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y %H:%M",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%Y-%m-%d",
    "%d %B %Y",
    "%d %b %Y",
)

_DATE_RE = re.compile(r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{4}-\d{1,2}-\d{1,2}")


def parse_date(text: Any) -> Optional[datetime]:
    """Parse common tender date expressions; None when unparseable."""
    if not text or not isinstance(text, str):
        return None
    t = text.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(t, fmt)
        except ValueError:
            continue
    m = _DATE_RE.search(t)
    if m:
        for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(m.group(0), fmt)
            except ValueError:
                continue
    return None


def _topic_key(title: str) -> str:
    """Coarse topic grouping key: alphanumerics only, truncated."""
    return re.sub(r"[^a-z0-9]", "", title.lower())[:60]


def _first_refs(req: Dict[str, Any], limit: int = 2) -> List[Dict[str, Any]]:
    refs = req.get("source_references") or []
    return [dict(r) for r in refs[:limit]]


def detect_missing_attachments(
    requirements: List[Dict[str, Any]], start_n: int = 1
) -> Tuple[List[Dict[str, Any]], int]:
    """One MISSING_ATTACHMENT issue per distinct missing document.

    Generic file phrases are deduplicated against specific documents: when a
    requirement already names an ATC/SLA/annexure, its "linked file" / "file
    attachment" / "buyer-added" phrases refer to that same file and do not
    spawn extra issues. Independently referenced documents (ATC vs SLA vs
    corrigendum) always stay separate issues.
    """
    by_label: Dict[str, Dict[str, Any]] = {}
    for req in requirements:
        labels = find_attachment_refs(
            req.get("title"),
            req.get("original_text"),
            (req.get("required_evidence") or []),
            req.get("applicability_condition"),
        )
        specific = [lb for lb in labels if lb in SPECIFIC_ATTACHMENT_LABELS]
        # Fold generic phrases into the specific document(s) on this req.
        effective = specific or labels
        for label in effective:
            entry = by_label.setdefault(label, {"ids": [], "refs": []})
            rid = str(req.get("requirement_id", "?"))
            if rid not in entry["ids"]:
                entry["ids"].append(rid)
            for r in _first_refs(req, 1):
                if len(entry["refs"]) < 3 and r not in entry["refs"]:
                    entry["refs"].append(r)
    issues: List[Dict[str, Any]] = []
    n = start_n
    for label, entry in sorted(by_label.items()):
        issues.append(
            {
                "issue_id": f"ISSUE-{n:03d}",
                "issue_type": "MISSING_ATTACHMENT",
                "description": (
                    f"Tender references '{label}' ({len(entry['ids'])} requirement(s): "
                    f"{', '.join(entry['ids'][:8])}) but the document was not supplied "
                    f"for analysis. Verify its contents before relying on affected clauses."
                ),
                "severity": "high" if len(entry["ids"]) > 2 else "medium",
                "affected_requirement_ids": entry["ids"],
                "source_references": entry["refs"],
                "requires_human_review": True,
            }
        )
        n += 1
    return issues, n


def detect_contradictions(
    requirements: List[Dict[str, Any]],
    metadata: Dict[str, Any],
    start_n: int = 1,
) -> Tuple[List[Dict[str, Any]], int]:
    """Conflicting flags, thresholds and deadline ordering (conservative)."""
    issues: List[Dict[str, Any]] = []
    n = start_n

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for req in requirements:
        title = str(req.get("title", "")).strip()
        if not title:
            continue
        groups.setdefault(_topic_key(title), []).append(req)

    for _, group in sorted(groups.items()):
        if len(group) < 2:
            continue
        flags = {str(r.get("requirement_id")): r.get("mandatory") for r in group}
        trues = sorted(i for i, v in flags.items() if v is True)
        falses = sorted(i for i, v in flags.items() if v is False)
        if trues and falses:
            refs: List[Dict[str, Any]] = []
            for r in group:
                refs.extend(_first_refs(r, 1))
            issues.append(
                {
                    "issue_id": f"ISSUE-{n:03d}",
                    "issue_type": "CONTRADICTION",
                    "description": (
                        f"Conflicting obligation flags for '{group[0].get('title')}': "
                        f"mandatory in {', '.join(trues)} but not mandatory in "
                        f"{', '.join(falses)}. Confirm which clause governs."
                    ),
                    "severity": "high",
                    "affected_requirement_ids": sorted(set(trues) | set(falses)),
                    "source_references": refs[:3],
                    "requires_human_review": True,
                }
            )
            n += 1

    sub = parse_date((metadata or {}).get("submission_deadline"))
    opening = parse_date((metadata or {}).get("bid_opening_date"))
    if sub and opening and sub > opening:
        issues.append(
            {
                "issue_id": f"ISSUE-{n:03d}",
                "issue_type": "CONTRADICTION",
                "description": (
                    f"Submission deadline ({metadata.get('submission_deadline')}) is after "
                    f"bid opening date ({metadata.get('bid_opening_date')}). Confirm the "
                    f"correct dates with the issuing authority."
                ),
                "severity": "high",
                "affected_requirement_ids": [],
                "source_references": [],
                "requires_human_review": True,
            }
        )
        n += 1

    # Threshold conflicts, grouped by measure (category + unit) but compared
    # ONLY between semantically equivalent titles. Sharing a category and
    # unit is not enough: minimum turnover and estimated bid value are
    # different financial concepts and must never be flagged together.
    by_measure: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for req in requirements:
        th = req.get("threshold") or {}
        if th.get("value") is None:
            continue
        unit = str(th.get("unit", "")).strip().lower()
        if not unit:
            continue
        by_measure.setdefault(
            (_enum_value(req.get("category", "")), unit), []
        ).append(req)
    for (category, unit), group in sorted(by_measure.items()):
        distinct: Dict[float, List[Dict[str, Any]]] = {}
        for r in group:
            distinct.setdefault(float(r["threshold"]["value"]), []).append(r)
        if len(distinct) > 1:
            vals = sorted(distinct.items())
            conflicting_ids: List[str] = []
            conflict_notes: List[str] = []
            seen_pairs: set = set()
            for ia, (va, ras) in enumerate(vals):
                for vb, rbs in vals[ia + 1:]:
                    for ra in ras:
                        for rb in rbs:
                            pair = tuple(sorted((
                                str(ra.get("requirement_id")),
                                str(rb.get("requirement_id")),
                            )))
                            if pair in seen_pairs:
                                continue
                            seen_pairs.add(pair)
                            if not titles_equivalent(
                                str(ra.get("title", "")), str(rb.get("title", ""))
                            ):
                                continue
                            conflicting_ids.extend(pair)
                            conflict_notes.append(
                                f"{pair[0]} states {ra['threshold'].get('operator', '=')} "
                                f"{fmt_number(va)} {unit} but {pair[1]} states "
                                f"{rb['threshold'].get('operator', '=')} {fmt_number(vb)} {unit}"
                            )
            if not conflict_notes:
                continue
            all_ids = sorted(set(conflicting_ids))
            id_set = set(all_ids)
            titles = sorted({
                str(r.get("title", ""))
                for r in group
                if str(r.get("requirement_id")) in id_set
            })
            refs: List[Dict[str, Any]] = []
            for r in group:
                if str(r.get("requirement_id")) in id_set:
                    refs.extend(_first_refs(r, 1))
            scope = f"'{titles[0]}'" if len(titles) == 1 else f"{category} ({unit})"
            issues.append(
                {
                    "issue_id": f"ISSUE-{n:03d}",
                    "issue_type": "CONTRADICTION",
                    "description": (
                        f"Conflicting thresholds for {scope}: "
                        + "; ".join(conflict_notes)
                        + ". Both constrain the same measure — confirm the governing value."
                    ),
                    "severity": "high",
                    "affected_requirement_ids": all_ids,
                    "source_references": refs[:3],
                    "requires_human_review": True,
                }
            )
            n += 1
    return issues, n


def detect_issues(
    requirements: List[Dict[str, Any]],
    metadata: Dict[str, Any],
    document_name: str = "",  # noqa: ARG001 — kept for call-site clarity
) -> List[Dict[str, Any]]:
    """Run all detectors; returns validated issue dicts with stable IDs."""
    from app.models.tender import TenderAnalysisIssue

    issues, n = detect_missing_attachments(requirements, start_n=1)
    contra, _ = detect_contradictions(requirements, metadata or {}, start_n=n)
    issues.extend(contra)
    validated: List[Dict[str, Any]] = []
    for iss in issues:
        try:
            validated.append(TenderAnalysisIssue(**iss).model_dump())
        except Exception:
            continue
    return validated
