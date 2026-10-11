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
) -> dict[str, Any]:
    """Lenient mapping -> strict RequirementEvidence dict.

    Falls back to deterministic derivation from retrieval hits when the
    model output is unusable: hits exist -> partial/needs_review,
    no hits -> missing. Never claims satisfied without model confirmation.
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

    confidence = _coerce_float(raw.get("confidence"))
    if confidence is None:
        confidence = 0.5 if hits else 0.0
    confidence = min(1.0, max(0.0, confidence))

    strict: dict[str, Any] = {
        "requirement_id": requirement_id,
        "requirement_title": requirement_title,
        "status": status,
        "confidence": confidence,
        "hits": hits,
        "computed_value": _coerce_float(raw.get("computed_value")),
        "computed_unit": (
            str(raw["computed_unit"])[:60] if raw.get("computed_unit") else None
        ),
        "notes": str(raw["notes"])[:1000] if raw.get("notes") else None,
    }
    return RequirementEvidence(**strict).model_dump()
