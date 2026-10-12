"""Lenient LLM-side schemas for evidence mapping.

Same philosophy as the analyst agent: flat scalar-only shapes for small
models under strict json_mode, then deterministic normalization to the
strict models in app/models/evidence.py.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.aliases import AliasChoices

from app.models.evidence import RequirementEvidence
from app.agents.evidence_agent.verification import (
    audit_numeric_claim,
    coerce_computed,
)


class LenientBase(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class LLMRequirementMapping(LenientBase):
    requirement_id: Any | None = Field(
        default=None,
        validation_alias=AliasChoices("requirement_id", "requirementId", "id"),
    )
    status: Any | None = None
    confidence: Any | None = None
    computed_value: Any | None = Field(
        default=None,
        validation_alias=AliasChoices("computed_value", "computedValue", "value"),
    )
    computed_unit: Any | None = Field(
        default=None,
        validation_alias=AliasChoices("computed_unit", "computedUnit", "unit"),
    )
    supporting_doc: Any | None = Field(
        default=None,
        validation_alias=AliasChoices("supporting_doc", "supportingDoc", "document"),
    )
    supporting_page: Any | None = None
    notes: Any | None = None


class LLMMappingList(LenientBase):
    mappings: list[LLMRequirementMapping] = Field(default_factory=list)


_VALID_STATUSES = {"satisfied", "partial", "missing", "needs_review"}


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (ValueError, TypeError):
        return None


def _coerce_int(value: Any, default: int = 1) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(float(value))
    except (ValueError, TypeError):
        return default


def normalize_mapping(
    item: Any,
    requirement_id: str,
    hits: list[dict[str, Any]],
    requirement_title: str | None = None,
    requirement: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Lenient mapping -> strict RequirementEvidence dict.

    Falls back to deterministic derivation from retrieval hits when the
    model output is unusable: hits exist -> partial/needs_review,
    no hits -> missing. Never claims satisfied without model confirmation.
    An LLM satisfied-claim whose own quoted figure contradicts the
    requirement threshold is downgraded to needs_review (audit).
    """
    if isinstance(item, BaseModel):
        raw = item.model_dump()
    elif isinstance(item, dict):
        raw = dict(item)
    else:
        raw = {}

    status = str(raw.get("status") or "").strip().lower()
    if status not in _VALID_STATUSES:
        # Deterministic fallback: evidence exists but unjudged -> needs_review.
        status = "needs_review" if hits else "missing"

    if not hits and status in ("satisfied", "partial"):
        # A verdict with no evidence behind it is void: without excerpts
        # there is nothing proving or half-proving the requirement.
        status = "missing"

    confidence = _coerce_float(raw.get("confidence"))
    if confidence is None:
        confidence = 0.5 if hits else 0.0
    confidence = min(1.0, max(0.0, confidence))

    computed_value = coerce_computed(raw.get("computed_value"))
    notes = str(raw["notes"])[:1000] if raw.get("notes") else None
    if not (notes and notes.strip()):
        # Every verdict carries a reason — no silent statuses, ever.
        doc_names = sorted({str(h.get("document_name", "?")) for h in hits})
        if status == "satisfied":
            notes = (
                f"Supported by {len(hits)} excerpt(s)"
                + (f" in {', '.join(doc_names[:3])}" if doc_names else "")
                + "."
            )
        elif status == "partial":
            notes = (
                "Partial support found in retrieved excerpts, but gaps remain; "
                "see excerpts for what is still missing."
            )
        elif status == "missing":
            notes = (
                "No supporting excerpt found in indexed company documents."
                if not hits
                else "Retrieved excerpts do not satisfy the requirement; see excerpts."
            )
        else:
            notes = "Requires human review — see retrieved excerpts."
    if requirement is not None:
        target = (requirement.get("threshold") or {}).get("value")
        if target is None:
            # No numeric target (exemptions, qualitative clauses): any
            # computed figure is noise by construction ("123456" IDs,
            # stray counts) — null it instead of laundering junk.
            computed_value = None
            raw["computed_unit"] = None
        status, confidence_adj, audit_note = audit_numeric_claim(
            requirement, status, raw.get("computed_value"), notes
        )
        if confidence_adj is not None:
            confidence = min(confidence, confidence_adj)
        if audit_note is not None:
            notes = audit_note

    strict: dict[str, Any] = {
        "requirement_id": requirement_id,
        "requirement_title": requirement_title,
        "status": status,
        "confidence": confidence,
        "hits": hits,
        "computed_value": computed_value,
        "computed_unit": (
            str(raw["computed_unit"])[:60] if raw.get("computed_unit") else None
        ),
        "notes": notes,
    }
    return RequirementEvidence(**strict).model_dump()
