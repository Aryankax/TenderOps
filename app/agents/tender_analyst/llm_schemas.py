"""Lenient LLM-side schemas + normalizers.

The strict API models in app/models/tender.py are too brittle for small
models (e.g. openai/gpt-oss-20b on Groq): the model emits alias field names
(requirementId/id, description, thresholds/deadlines/evidence/page/section/
note), string values for booleans ("mandatory"/"conditional"), thresholds as
plain strings ("180 days", "115637000 INR"), and explicit nulls for list
fields. Strict parsing then raises 45-50 pydantic validation errors per
chunk, and forced tool calling fails with "did not call a tool" / truncated
tool-call arguments.

Strategy: parse with these forgiving schemas (extra="ignore", all optional,
AliasChoices for observed variants), then deterministically normalize to the
strict TenderMetadata / TenderRequirement dicts.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field
from pydantic.aliases import AliasChoices

from app.models.tender import RequirementCategory, TenderMetadata, TenderRequirement
from app.core.sanitize import sanitize_text


def _clean(value: Any, limit: int | None = None) -> Any:
    """Sanitize output strings (control chars must never reach the API)."""
    if not isinstance(value, str):
        return value
    value = sanitize_text(value)
    return value[:limit] if limit is not None else value


class LenientBase(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class LLMThreshold(LenientBase):
    operator: Optional[str] = None
    value: Optional[Any] = None
    unit: Optional[str] = None
    original_value: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("original_value", "originalValue"),
    )
    # Observed variant: {"value": "115637000", "currency": "INR"}
    currency: Optional[str] = None


class LLMSourceReference(LenientBase):
    document_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("document_name", "documentName"),
    )
    page_number: Optional[Any] = Field(
        default=None,
        validation_alias=AliasChoices("page_number", "pageNumber", "page"),
    )
    section_heading: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("section_heading", "sectionHeading", "section"),
    )
    source_excerpt: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("source_excerpt", "sourceExcerpt", "excerpt"),
    )


class LLMRequirement(LenientBase):
    requirement_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("requirement_id", "requirementId", "id"),
    )
    category: Optional[str] = None
    title: Optional[str] = Field(
        default=None, validation_alias=AliasChoices("title", "name")
    )
    # Model often puts the human-readable text in "description".
    description: Optional[str] = None
    original_text: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("original_text", "originalText"),
    )
    normalized_description: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "normalized_description", "normalizedDescription"
        ),
    )
    mandatory: Optional[Any] = None
    applicability_condition: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "applicability_condition", "applicabilityCondition", "condition", "note"
        ),
    )
    threshold: Optional[Any] = None
    # Observed plural variant: "thresholds": [{...}] or ["180 days"]
    thresholds: Optional[Any] = None
    required_evidence: Optional[Any] = Field(
        default=None,
        validation_alias=AliasChoices(
            "required_evidence", "requiredEvidence", "evidence"
        ),
    )
    source_references: Optional[Any] = Field(
        default=None,
        validation_alias=AliasChoices(
            "source_references",
            "sourceReferences",
            "sources",
            "references",
        ),
    )
    depends_on: Optional[Any] = Field(
        default=None, validation_alias=AliasChoices("depends_on", "dependsOn")
    )
    confidence: Optional[Any] = None
    needs_review: Optional[Any] = Field(
        default=None, validation_alias=AliasChoices("needs_review", "needsReview")
    )
    review_reason: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("review_reason", "reviewReason"),
    )
    # Observed flat fallbacks (model puts page/section at requirement level).
    page: Optional[Any] = None
    section: Optional[str] = None
    # Observed date/evidence variants.
    deadlines: Optional[Any] = None
    deadline: Optional[Any] = None
    notes: Optional[Any] = None


class LLMRequirementList(LenientBase):
    requirements: List[LLMRequirement] = Field(default_factory=list)


class LLMSimpleRequirement(LenientBase):
    """Flat scalar-only requirement shape — the primary extraction schema.

    Small models (gpt-oss-20b) cannot reliably emit nested JSON (threshold
    objects, source_reference arrays) under Groq's strict json_mode
    validation, producing recurring 400 json_validate_failed errors with
    empty failed_generation. Every field here accepts plain scalars only;
    nesting (threshold object, source_references array) is built in code by
    normalize_requirement(). Aliases absorb the model's naming drift.
    """

    requirement_id: Optional[Any] = Field(
        default=None,
        validation_alias=AliasChoices("requirement_id", "requirementId", "id"),
    )
    category: Optional[Any] = None
    title: Optional[Any] = Field(
        default=None, validation_alias=AliasChoices("title", "name")
    )
    description: Optional[Any] = None
    original_text: Optional[Any] = Field(
        default=None,
        validation_alias=AliasChoices("original_text", "originalText", "text"),
    )
    mandatory: Optional[Any] = None
    condition: Optional[Any] = Field(
        default=None,
        validation_alias=AliasChoices(
            "condition",
            "applicability_condition",
            "applicabilityCondition",
            "note",
        ),
    )
    # Raw threshold prose ("115637000 INR", "180 days", "7 years") — parsed
    # in code by parse_threshold(). Accepts dicts/lists too, just in case.
    threshold_text: Optional[Any] = Field(
        default=None,
        validation_alias=AliasChoices(
            "threshold_text", "thresholdText", "threshold", "thresholds", "value"
        ),
    )
    evidence: Optional[Any] = Field(
        default=None,
        validation_alias=AliasChoices(
            "evidence", "required_evidence", "requiredEvidence"
        ),
    )
    deadline: Optional[Any] = Field(
        default=None, validation_alias=AliasChoices("deadline", "deadlines")
    )
    page: Optional[Any] = None
    section: Optional[Any] = Field(
        default=None,
        validation_alias=AliasChoices("section", "section_heading", "sectionHeading"),
    )


class LLMSimpleRequirementList(LenientBase):
    requirements: List[LLMSimpleRequirement] = Field(default_factory=list)


class LLMMetadata(LenientBase):
    # All fields are Any: the model returns numbers for amount/date fields
    # (e.g. "emd_amount": 9874275). normalize_metadata() stringifies them.
    tender_title: Optional[Any] = None
    tender_reference: Optional[Any] = None
    issuing_authority: Optional[Any] = None
    submission_deadline: Optional[Any] = None
    bid_opening_date: Optional[Any] = None
    pre_bid_meeting_date: Optional[Any] = None
    clarification_deadline: Optional[Any] = None
    emd_amount: Optional[Any] = None
    tender_fee: Optional[Any] = None
    currency: Optional[Any] = None
    contract_duration: Optional[Any] = None
    delivery_deadline: Optional[Any] = None
    other_metadata: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Normalizers: lenient LLM output -> strict API dicts
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")

_CATEGORY_KEYWORDS = {
    "FINANCIAL_ELIGIBILITY": ("FINANCIAL", "TURNOVER", "EMD", "BID SECURITY", "NET WORTH"),
    "TECHNICAL_ELIGIBILITY": ("TECHNICAL", "ELIGIBILITY"),
    "EXPERIENCE": ("EXPERIENCE", "PAST PERFORMANCE", "TRACK RECORD"),
    "LEGAL_AND_STATUTORY": ("LEGAL", "STATUTORY", "COMPLIANCE", "GST", "PAN", "LICENSE"),
    "MANDATORY_DOCUMENTS": ("DOCUMENT", "CERTIFICATE", "EVIDENCE", "ENCLOSE", "UPLOAD"),
    "TECHNICAL_SPECIFICATIONS": ("SPECIFICATION", "SCOPE OF WORK", "SCOPE"),
    "COMMERCIAL": ("COMMERCIAL", "PRICE", "PAYMENT", "COST"),
    "DEADLINES": ("DEADLINE", "DUE DATE", "VALIDITY", "END DATE", "SUBMISSION DATE"),
    "SUBMISSION_INSTRUCTIONS": ("SUBMIT", "SUBMISSION", "BID EXTENSION", "INSTRUCTION"),
    "CONTRACTUAL": ("CONTRACT", "WARRANTY", "PENALTY", "LIABILITY", "MSE", "STARTUP", "EXEMPTION", "RELAXATION"),
}


def _as_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                # e.g. evidence given as [{"value": ...}] — stringify best effort
                text = item.get("source_excerpt") or item.get("text") or item.get("value") or str(item)
                out.append(str(text))
            else:
                out.append(str(item))
        return out
    return [str(value)]


def _coerce_int(value: Any, default: int) -> int:
    try:
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return int(value)
        m = _NUMBER_RE.search(str(value))
        return int(float(m.group(0).replace(",", ""))) if m else default
    except (ValueError, TypeError, AttributeError):
        return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return float(value)
        m = _NUMBER_RE.search(str(value).replace(",", ""))
        return float(m.group(0)) if m else default
    except (ValueError, TypeError, AttributeError):
        return default


def parse_mandatory(value: Any) -> Tuple[Optional[bool], Optional[str]]:
    """Map bool | "mandatory"/"optional"/"conditional" strings -> (bool|None, condition)."""
    if value is None:
        return None, None
    if isinstance(value, bool):
        return value, None
    text = str(value).strip().lower()
    if text in ("true", "yes", "mandatory", "required", "must", "essential"):
        return True, None
    if text in ("false", "no", "optional", "not mandatory", "not required"):
        return False, None
    if "condition" in text or text in ("conditional", "partial", "conditional/partial"):
        return None, str(value).strip()
    # Unknown strings like "mandatory (partial)" — treat as conditional note.
    if "partial" in text or "ambigu" in text or "exempt" in text:
        return None, str(value).strip()
    return None, str(value).strip() or None


def parse_category(value: Any) -> str:
    if not value:
        return RequirementCategory.OTHER.value
    text = str(value).strip().upper().replace(" ", "_").replace("-", "_")
    valid = {c.value for c in RequirementCategory}
    if text in valid:
        return text
    for enum_value, keywords in _CATEGORY_KEYWORDS.items():
        if any(k in str(value).upper() for k in keywords):
            return enum_value
    return RequirementCategory.OTHER.value


def parse_threshold(value: Any) -> Optional[Dict[str, Any]]:
    """Accept dict | numeric string ("180 days", "115637000 INR") | list (take first)."""
    if value is None:
        return None
    if isinstance(value, list):
        if not value:
            return None
        value = value[0]
    if isinstance(value, dict):
        raw_val = value.get("value")
        unit = value.get("unit") or value.get("currency") or ""
        original = (
            value.get("original_value")
            or value.get("originalValue")
            or str(value.get("value", ""))
        )
        return {
            "operator": value.get("operator") or "=",
            "value": _coerce_float(raw_val),
            "unit": _clean(str(unit or ""), 100),
            "original_value": _clean(str(original or ""), 500),
        }
    # Plain string / number, e.g. "180 days", "115637000 INR (partial relaxation)"
    text = _clean(str(value), 500)
    m = _NUMBER_RE.search(text.replace(",", ""))
    num = float(m.group(0)) if m else 0.0
    unit = text[m.end():].strip(" ()") if m else ""
    return {"operator": "=", "value": num, "unit": unit, "original_value": text}


def _normalize_source_ref(
    item: Any, document_name: str, default_page: int, fallback_excerpt: str
) -> Dict[str, Any]:
    if isinstance(item, dict):
        page = item.get("page_number", item.get("pageNumber", item.get("page")))
        section = item.get("section_heading", item.get("sectionHeading", item.get("section")))
        excerpt = item.get("source_excerpt", item.get("sourceExcerpt", item.get("excerpt")))
        doc = item.get("document_name", item.get("documentName")) or document_name
    elif isinstance(item, BaseModel):
        d = item.model_dump()
        page = d.get("page_number", d.get("page"))
        section = d.get("section_heading", d.get("section"))
        excerpt = d.get("source_excerpt", d.get("excerpt"))
        doc = d.get("document_name") or document_name
    else:
        page, section, excerpt, doc = default_page, None, None, document_name
    return {
        "document_name": str(doc or document_name),
        "page_number": _coerce_int(page, default_page),
        "section_heading": _clean(str(section), 500) if section else None,
        "source_excerpt": _clean(str(excerpt or fallback_excerpt or ""), 2000),
    }


def normalize_metadata(data: Any) -> Dict[str, Any]:
    """Lenient LLM metadata -> strict TenderMetadata dict (never raises on extras)."""
    if isinstance(data, BaseModel):
        raw = data.model_dump()
    elif hasattr(data, "model_dump") and callable(getattr(data, "model_dump")):
        try:
            raw = dict(data.model_dump())
        except Exception:
            raw = {}
    elif isinstance(data, dict):
        raw = dict(data)
    else:
        raw = {}
    allowed = set(TenderMetadata.model_fields)
    clean = {k: v for k, v in raw.items() if k in allowed}
    for k, v in clean.items():
        if v is not None and not isinstance(v, str) and k != "other_metadata":
            clean[k] = _clean(str(v), 2000)
        elif isinstance(v, str):
            clean[k] = _clean(v, 2000)
    if isinstance(clean.get("other_metadata"), dict):
        clean["other_metadata"] = {
            _clean(str(k), 200): _clean(str(v), 2000) if isinstance(v, str) else v
            for k, v in clean["other_metadata"].items()
        }
    if not isinstance(clean.get("other_metadata"), dict):
        # Keep stray keys instead of dropping them.
        extras = {k: v for k, v in raw.items() if k not in allowed}
        clean["other_metadata"] = extras if isinstance(extras, dict) else {}
    return TenderMetadata(**clean).model_dump()


def _simple_to_canonical(d: Dict[str, Any]) -> Dict[str, Any]:
    """Map flat simple-schema keys onto canonical LLMRequirement keys."""
    d = dict(d)
    if d.get("threshold") is None and d.get("threshold_text") is not None:
        d["threshold"] = d["threshold_text"]
    d.pop("threshold_text", None)
    if d.get("deadlines") is None and d.get("deadline") is not None:
        d["deadlines"] = d["deadline"]
    if d.get("applicability_condition") is None and d.get("condition") is not None:
        d["applicability_condition"] = d["condition"]
    if d.get("required_evidence") is None and d.get("evidence") is not None:
        d["required_evidence"] = d["evidence"]
    return d


def normalize_requirement(
    item: Any,
    document_name: str,
    default_page: int = 1,
    index: int = 0,
) -> Dict[str, Any]:
    """Lenient LLM requirement (model | dict | mock) -> strict TenderRequirement dict."""
    if isinstance(item, LLMSimpleRequirement):
        # Flat primary shape: remap to canonical keys, then run the same path.
        item = _simple_to_canonical(item.model_dump())
    if hasattr(item, "model_dump") and callable(getattr(item, "model_dump")) and not isinstance(item, LLMRequirement):
        # Duck-typed strict shapes (e.g. test mocks whose model_dump returns a
        # strict dict): validate directly, fall back to raw-dict parsing.
        try:
            return TenderRequirement(**item.model_dump()).model_dump()
        except Exception:
            try:
                raw = dict(item.model_dump())
            except Exception:
                raw = {}
        # fall through with raw dict
    elif isinstance(item, LLMRequirement):
        raw = item.model_dump()
    elif isinstance(item, dict):
        # Re-parse through the lenient model to apply aliases.
        try:
            raw = LLMRequirement(**_simple_to_canonical(item)).model_dump()
        except Exception:
            raw = _simple_to_canonical(dict(item))
    else:
        raw = {}

    def _pick(*keys: str) -> Any:
        for k in keys:
            if raw.get(k) is not None:
                return raw.get(k)
        return None

    req_id = _pick("requirement_id", "requirementId", "id") or f"R-{index + 1}"
    title = _pick("title", "name") or _pick("description") or ""
    original = (
        _pick("original_text", "originalText")
        or _pick("description")
        or _pick("title", "name")
        or ""
    )
    if not title:
        title = str(original)[:120] or f"Requirement {index + 1}"

    mandatory_raw = _pick("mandatory")
    mandatory, condition = parse_mandatory(mandatory_raw)
    extra_condition = _pick("applicability_condition", "condition", "note")
    notes = _pick("notes")
    deadlines = _as_str_list(_pick("deadlines", "deadline"))
    applicability_parts = [
        p for p in [condition, extra_condition] if p
    ]
    if notes:
        applicability_parts.extend(_as_str_list(notes))
    if deadlines:
        applicability_parts.append("Deadlines: " + "; ".join(deadlines))
    applicability = "; ".join(applicability_parts) or None

    threshold_raw = _pick("threshold")
    if threshold_raw is None:
        threshold_raw = _pick("thresholds")
    threshold = parse_threshold(threshold_raw)

    evidence = _as_str_list(_pick("required_evidence", "evidence"))

    excerpt_fallback = str(original)[:2000]
    refs_raw = _pick("source_references", "sources", "references")
    refs: List[Dict[str, Any]] = []
    if isinstance(refs_raw, list) and refs_raw:
        flat_page = _pick("page", "page_number", "pageNumber")
        try:
            default_ref_page = _coerce_int(flat_page, default_page)
        except Exception:
            default_ref_page = default_page
        for r in refs_raw:
            refs.append(
                _normalize_source_ref(r, document_name, default_ref_page, excerpt_fallback)
            )
    else:
        flat_page = _pick("page", "page_number", "pageNumber")
        flat_section = _pick("section", "section_heading", "sectionHeading")
        if flat_page is not None or flat_section or excerpt_fallback:
            refs.append(
                {
                    "document_name": document_name,
                    "page_number": _coerce_int(flat_page, default_page),
                    "section_heading": str(flat_section) if flat_section else None,
                    "source_excerpt": excerpt_fallback,
                }
            )

    needs_review_raw = _pick("needs_review")
    if isinstance(needs_review_raw, bool):
        needs_review = needs_review_raw
    elif isinstance(needs_review_raw, str):
        needs_review = needs_review_raw.strip().lower() in ("true", "yes", "1")
    else:
        needs_review = False
    review_reason = _pick("review_reason")
    synthesized_id = not _pick("requirement_id", "requirementId", "id")
    if (synthesized_id or applicability_parts) and not needs_review:
        # Conditional / ambiguous items and id-less items need human review,
        # but preserve an explicit False only when nothing is suspicious.
        if applicability_parts or synthesized_id:
            needs_review = True
            if not review_reason:
                review_reason = (
                    "Conditional or ambiguous requirement"
                    if applicability_parts
                    else "Requirement ID was auto-generated"
                )

    depends = _as_str_list(_pick("depends_on"))
    confidence_raw = _pick("confidence")
    try:
        confidence = (
            float(confidence_raw)
            if confidence_raw is not None and not isinstance(confidence_raw, bool)
            else None
        )
    except (ValueError, TypeError):
        confidence = None

    strict = {
        "requirement_id": _clean(str(req_id), 100),
        "category": parse_category(_pick("category")),
        "title": _clean(str(title), 500),
        "original_text": _clean(str(original), 4000) or _clean(str(title), 4000),
        "normalized_description": _clean(_pick("normalized_description"), 2000),
        "mandatory": mandatory,
        "applicability_condition": _clean(applicability, 2000),
        "threshold": threshold,
        "required_evidence": [_clean(e, 1000) for e in evidence],
        "source_references": refs,
        "depends_on": [_clean(d, 200) for d in depends],
        "confidence": confidence,
        "needs_review": needs_review,
        "review_reason": _clean(review_reason, 1000),
    }
    return TenderRequirement(**strict).model_dump()


def normalize_requirement_list(
    data: Any, document_name: str, default_page: int = 1
) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        items = data.get("requirements", []) or []
    else:
        # Lenient models, Strict models, and duck-typed mocks (MagicMock with
        # .requirements) all expose the list as an attribute.
        try:
            items = getattr(data, "requirements", []) or []
        except Exception:
            items = []
    if isinstance(items, dict):
        items = [items]
    out: List[Dict[str, Any]] = []
    for idx, item in enumerate(items):
        try:
            out.append(normalize_requirement(item, document_name, default_page, idx))
        except Exception:
            continue  # skip unrecoverable items, keep the rest
    return out
