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

import logging
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.aliases import AliasChoices

from app.core.sanitize import sanitize_text
from app.models.tender import RequirementCategory, TenderMetadata, TenderRequirement

logger = logging.getLogger(__name__)


def _clean(value: Any, limit: int | None = None) -> Any:
    """Sanitize output strings (control chars must never reach the API)."""
    if not isinstance(value, str):
        return value
    value = sanitize_text(value)
    return value[:limit] if limit is not None else value


class LenientBase(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class LLMThreshold(LenientBase):
    operator: str | None = None
    value: Any | None = None
    unit: str | None = None
    original_value: str | None = Field(
        default=None,
        validation_alias=AliasChoices("original_value", "originalValue"),
    )
    # Observed variant: {"value": "115637000", "currency": "INR"}
    currency: str | None = None


class LLMSourceReference(LenientBase):
    document_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("document_name", "documentName"),
    )
    page_number: Any | None = Field(
        default=None,
        validation_alias=AliasChoices("page_number", "pageNumber", "page"),
    )
    section_heading: str | None = Field(
        default=None,
        validation_alias=AliasChoices("section_heading", "sectionHeading", "section"),
    )
    source_excerpt: str | None = Field(
        default=None,
        validation_alias=AliasChoices("source_excerpt", "sourceExcerpt", "excerpt"),
    )


class LLMRequirement(LenientBase):
    requirement_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("requirement_id", "requirementId", "id"),
    )
    category: str | None = None
    semantic_type: Any | None = Field(
        default=None,
        validation_alias=AliasChoices("semantic_type", "semanticType"),
    )
    title: str | None = Field(
        default=None, validation_alias=AliasChoices("title", "name")
    )
    # Model often puts the human-readable text in "description".
    description: str | None = None
    original_text: str | None = Field(
        default=None,
        validation_alias=AliasChoices("original_text", "originalText"),
    )
    normalized_description: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "normalized_description", "normalizedDescription"
        ),
    )
    mandatory: Any | None = None
    applicability_condition: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "applicability_condition", "applicabilityCondition", "condition", "note"
        ),
    )
    threshold: Any | None = None
    # Observed plural variant: "thresholds": [{...}] or ["180 days"]
    thresholds: Any | None = None
    required_evidence: Any | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "required_evidence", "requiredEvidence", "evidence"
        ),
    )
    source_references: Any | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "source_references",
            "sourceReferences",
            "sources",
            "references",
        ),
    )
    depends_on: Any | None = Field(
        default=None, validation_alias=AliasChoices("depends_on", "dependsOn")
    )
    confidence: Any | None = None
    needs_review: Any | None = Field(
        default=None, validation_alias=AliasChoices("needs_review", "needsReview")
    )
    review_reason: str | None = Field(
        default=None,
        validation_alias=AliasChoices("review_reason", "reviewReason"),
    )
    # Observed flat fallbacks (model puts page/section at requirement level).
    page: Any | None = None
    section: str | None = None
    # Observed date/evidence variants.
    deadlines: Any | None = None
    deadline: Any | None = None
    notes: Any | None = None


class LLMRequirementList(LenientBase):
    requirements: list[LLMRequirement] = Field(default_factory=list)


class LLMSimpleRequirement(LenientBase):
    """Flat scalar-only requirement shape — the primary extraction schema.

    Small models (gpt-oss-20b) cannot reliably emit nested JSON (threshold
    objects, source_reference arrays) under Groq's strict json_mode
    validation, producing recurring 400 json_validate_failed errors with
    empty failed_generation. Every field here accepts plain scalars only;
    nesting (threshold object, source_references array) is built in code by
    normalize_requirement(). Aliases absorb the model's naming drift.
    """

    requirement_id: Any | None = Field(
        default=None,
        validation_alias=AliasChoices("requirement_id", "requirementId", "id"),
    )
    category: Any | None = None
    semantic_type: Any | None = Field(
        default=None,
        validation_alias=AliasChoices("semantic_type", "semanticType"),
    )
    title: Any | None = Field(
        default=None, validation_alias=AliasChoices("title", "name")
    )
    description: Any | None = None
    original_text: Any | None = Field(
        default=None,
        validation_alias=AliasChoices("original_text", "originalText", "text"),
    )
    mandatory: Any | None = None
    condition: Any | None = Field(
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
    threshold_text: Any | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "threshold_text", "thresholdText", "threshold", "thresholds", "value"
        ),
    )
    evidence: Any | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "evidence", "required_evidence", "requiredEvidence"
        ),
    )
    deadline: Any | None = Field(
        default=None, validation_alias=AliasChoices("deadline", "deadlines")
    )
    page: Any | None = None
    section: Any | None = Field(
        default=None,
        validation_alias=AliasChoices("section", "section_heading", "sectionHeading"),
    )


class LLMSimpleRequirementList(LenientBase):
    requirements: list[LLMSimpleRequirement] = Field(default_factory=list)


class LLMMetadata(LenientBase):
    # All fields are Any: the model returns numbers for amount/date fields
    # (e.g. "emd_amount": 9874275). normalize_metadata() stringifies them.
    tender_title: Any | None = None
    tender_reference: Any | None = None
    issuing_authority: Any | None = None
    submission_deadline: Any | None = None
    bid_opening_date: Any | None = None
    pre_bid_meeting_date: Any | None = None
    clarification_deadline: Any | None = None
    emd_amount: Any | None = None
    tender_fee: Any | None = None
    currency: Any | None = None
    contract_duration: Any | None = None
    delivery_deadline: Any | None = None
    other_metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Normalizers: lenient LLM output -> strict API dicts
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")

_CATEGORY_KEYWORDS = {
    "FINANCIAL_ELIGIBILITY": (
        "FINANCIAL",
        "TURNOVER",
        "EMD",
        "BID SECURITY",
        "NET WORTH",
    ),
    "TECHNICAL_ELIGIBILITY": ("TECHNICAL", "ELIGIBILITY"),
    "EXPERIENCE": ("EXPERIENCE", "PAST PERFORMANCE", "TRACK RECORD"),
    "LEGAL_AND_STATUTORY": (
        "LEGAL",
        "STATUTORY",
        "COMPLIANCE",
        "GST",
        "PAN",
        "LICENSE",
    ),
    "MANDATORY_DOCUMENTS": ("DOCUMENT", "CERTIFICATE", "EVIDENCE", "ENCLOSE", "UPLOAD"),
    "TECHNICAL_SPECIFICATIONS": ("SPECIFICATION", "SCOPE OF WORK", "SCOPE"),
    "COMMERCIAL": ("COMMERCIAL", "PRICE", "PAYMENT", "COST"),
    "DEADLINES": ("DEADLINE", "DUE DATE", "VALIDITY", "END DATE", "SUBMISSION DATE"),
    "SUBMISSION_INSTRUCTIONS": ("SUBMIT", "SUBMISSION", "BID EXTENSION", "INSTRUCTION"),
    "CONTRACTUAL": (
        "CONTRACT",
        "WARRANTY",
        "PENALTY",
        "LIABILITY",
        "MSE",
        "STARTUP",
        "EXEMPTION",
        "RELAXATION",
    ),
}


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                # e.g. evidence given as [{"value": ...}] — stringify best effort
                text = (
                    item.get("source_excerpt")
                    or item.get("text")
                    or item.get("value")
                    or str(item)
                )
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


def parse_mandatory(value: Any) -> tuple[bool | None, str | None]:
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


def parse_threshold(
    value: Any, context: str = ""
) -> tuple[dict[str, Any] | None, str | None]:
    """Parse a threshold into (threshold_dict_or_None, review_flag_or_None).

    - Infers the comparison operator from comparison words in the threshold
      text *and* surrounding context (title/original text): minimum-style
      wording -> ">=", maximum-style -> "<=", exact -> "=", approximate -> "~".
    - Scales Indian monetary units: lakh/lac x1e5, crore x1e7 (unit recorded
      as INR; the verbatim expression is always preserved in original_value).
    - NEVER converts an unknown/non-numeric threshold into zero: value stays
      None with operator "unknown", and a review flag is returned.
    """
    if value is None:
        return None, None
    if isinstance(value, list):
        if not value:
            return None, None
        value = value[0]
    if isinstance(value, dict):
        raw_val = value.get("value")
        unit = value.get("unit") or value.get("currency") or ""
        original = (
            value.get("original_value")
            or value.get("originalValue")
            or str(value.get("value", ""))
        )
        number = _extract_number(raw_val)
        if number is None:
            number = _extract_number(original)
        if number is None:
            return (
                {
                    "operator": "unknown",
                    "value": None,
                    "unit": _clean(str(unit or ""), 100),
                    "original_value": _clean(str(original or ""), 500),
                },
                f"Threshold '{original}' has no parseable numeric value",
            )
        scaled, unit_out = _apply_unit_scale(number, f"{original} {unit}")
        return (
            {
                "operator": value.get("operator")
                or _infer_operator(f"{original} {unit} {context}", has_number=True),
                "value": scaled,
                "unit": _clean(unit_out, 100),
                "original_value": _clean(str(original or ""), 500),
            },
            None,
        )
    # Plain string / number, e.g. "minimum 115637000 INR", "within 45 days".
    text = _clean(str(value), 500)
    if _looks_like_date(text):
        return (
            {
                "operator": "unknown",
                "value": None,
                "unit": "",
                "original_value": text,
            },
            f"Threshold '{text}' looks like a date, not a numeric threshold",
        )
    number = _extract_number(text)
    if number is None:
        return (
            {
                "operator": "unknown",
                "value": None,
                "unit": "",
                "original_value": text,
            },
            f"Threshold '{text}' has no parseable numeric value",
        )
    scaled, unit_out = _apply_unit_scale(number, text)
    full_text = f"{text} {context}"
    return (
        {
            "operator": _infer_operator(full_text, has_number=True),
            "value": scaled,
            "unit": unit_out,
            "original_value": text,
        },
        None,
    )


def _extract_number(text: Any) -> float | None:
    """First parseable number in text, or None — never a guessed zero."""
    try:
        if isinstance(text, bool):
            return None
        if isinstance(text, (int, float)):
            return float(text)
        m = _NUMBER_RE.search(str(text).replace(",", ""))
        return float(m.group(0)) if m else None
    except (ValueError, TypeError, AttributeError):
        return None


def _apply_unit_scale(number: float, text: Any) -> tuple[float, str]:
    """Scale lakh/lac/crore into value; return (value, unit).

    The verbatim expression always survives in original_value, so expressing
    "1156.37 Lakh" as value=115637000.0/unit="INR" loses nothing.
    """
    s = str(text)
    stripped = s.replace(",", "")
    m = _NUMBER_RE.search(stripped)
    remainder = stripped[m.end() :] if m else s
    low = remainder.lower()
    scale = 1.0
    if re.search(r"\bcrore?s?\b", low):
        scale = 1e7
        remainder = re.sub(r"(?i)\bcrore?s?\b", "", remainder)
    elif re.search(r"\b(lakh|lac)s?\b", low):
        scale = 1e5
        remainder = re.sub(r"(?i)\b(lakh|lac)s?\b", "", remainder)
    unit = _clean_unit(remainder)
    if scale != 1.0 and unit.lower() in (
        "",
        "rs",
        "rs.",
        "inr",
        "\u20b9",
        "rupees",
        "re",
        "inr.",
    ):
        unit = "INR"
    # Round to paise: kills float dust (1156.37*1e5 -> 115636999.99999999).
    return round(number * scale, 2), unit


def _clean_unit(remainder: str) -> str:
    """Tidy a unit fragment: drop "(qualifiers)", commas, extra spaces."""
    s = re.split(r"[\(,;\|]", remainder)[0].strip()
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:60]


def _looks_like_date(text: str) -> bool:
    """Spot date-like strings so they are never parsed as numeric thresholds."""
    t = text.strip()
    return bool(
        re.match(r"^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", t)
        or re.match(r"^\d{4}-\d{1,2}-\d{1,2}", t)
    )


def _reframe_exemption_threshold(
    threshold: dict[str, Any],
    original: str,
    condition: str | None,
    prior_flag: str | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Reframe an exemption's numeric threshold as scope reference (P2).

    A relaxation "up to 115637000 INR" describes the base requirement being
    relieved, not a floor the bidder must clear. The number is preserved
    verbatim in original_value, but value/operator are withdrawn so the
    exemption can never read as an obligation — nor collide in contradiction
    detection. Full exemption is only stated when the source says so. Any
    pre-existing parse flag is preserved alongside the scope flag.
    """
    blob = f"{original} {condition or ''} {threshold.get('original_value', '')}".lower()
    reframed = {
        "operator": "unknown",
        "value": None,
        "unit": threshold.get("unit", ""),
        "original_value": threshold.get("original_value", ""),
    }
    if re.search(r"\b(partial|partly|proportionate|relaxation|relaxed|rebate)\b", blob):
        scope_flag: str | None = (
            f"Partial exemption scope '{reframed['original_value']}' — verify extent of "
            f"relief; never treated as full exemption"
        )
    elif re.search(
        r"\b(full|fully|complete(?:ly)?|entire(?:ly)?|100\s*%|wholly)\b", blob
    ):
        scope_flag = None
    else:
        scope_flag = f"Exemption scope unclear '{reframed['original_value']}' — verify extent of relief"
    if prior_flag and prior_flag not in (scope_flag or ""):
        combined = f"{scope_flag}; {prior_flag}" if scope_flag else prior_flag
        return reframed, combined
    return reframed, scope_flag


def link_exemptions(
    requirements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach each exemption to its base requirement (P2).

    Matches on same category + title equivalence with a non-exemption
    requirement holding a numeric threshold (e.g. MSE turnover relaxation ->
    minimum turnover). Sets depends_on and rewrites the description to name
    the base explicitly. Idempotent: already-linked exemptions are skipped.
    """
    for req in requirements:
        if req.get("semantic_type") != "EXEMPTION":
            continue
        desc = str(req.get("normalized_description") or "")
        if req.get("depends_on") and "relief from" in desc:
            continue  # already linked by a previous pass
        base = None
        for cand in requirements:
            if cand is req or cand.get("semantic_type") == "EXEMPTION":
                continue
            if _enum_value(cand.get("category")) != _enum_value(req.get("category")):
                continue
            if (cand.get("threshold") or {}).get("value") is None:
                continue
            if titles_equivalent(str(cand.get("title", "")), str(req.get("title", ""))):
                base = cand
                break
        if base is None:
            continue
        base_id = str(base.get("requirement_id"))
        req["depends_on"] = [base_id]
        blob = (
            f"{req.get('original_text', '')} {req.get('applicability_condition') or ''}"
        ).lower()
        partial = bool(
            re.search(
                r"\b(partial|partly|proportionate|relaxation|relaxed|rebate)\b", blob
            )
        )
        base_title = str(base.get("title", "")).strip()
        cond = (req.get("applicability_condition") or "").strip()
        desc = (
            f"Exemption ({'partial' if partial else 'unspecified'} relief from "
            f"{base_id} — {base_title}): {str(req.get('title', '')).strip()}."
        )
        if cond:
            desc += f" Applies when: {cond}."
        ev = req.get("required_evidence") or []
        if ev:
            desc += " Provide: " + "; ".join(ev) + "."
        req["normalized_description"] = _clean(desc, 2000)
        flag = (
            "Partial exemption scope — verify extent of relief"
            if partial
            else "Exemption scope unclear — verify extent of relief and base requirement"
        )
        reasons = req.get("review_reason") or ""
        if flag not in reasons:
            req["review_reason"] = _clean((reasons + "; " + flag).strip("; "), 1000)
        req["needs_review"] = True
    return requirements


_MIN_RE = re.compile(
    r"\b(minimum|min|at least|atleast|not less than|greater than|more than|"
    r"above|over|exceeding|in excess of)\b|>="
)
_MAX_RE = re.compile(
    r"\b(maximum|max|up to|upto|at most|atmost|not more than|not exceeding|"
    r"within|no more than|less than|below|under|capped at|ceiling)\b|<="
)
_APPROX_RE = re.compile(r"\b(about|around|approx(?:imate(?:ly)?)?|roughly|circa)\b|~")
_EXACT_RE = re.compile(r"\b(exactly|equal to|equals)\b|(?<![<>!])=(?!=)")


def _infer_operator(text: str, has_number: bool = True) -> str:
    """Infer >=, <=, =, ~ from comparison wording. Defaults to exact."""
    low = text.lower()
    if _MIN_RE.search(low):
        return ">="
    if _MAX_RE.search(low):
        return "<="
    if _APPROX_RE.search(low):
        return "~"
    if _EXACT_RE.search(low):
        return "="
    return "=" if has_number else "unknown"


def _normalize_source_ref(
    item: Any, document_name: str, default_page: int, fallback_excerpt: str
) -> dict[str, Any]:
    if isinstance(item, dict):
        page = item.get("page_number", item.get("pageNumber", item.get("page")))
        section = item.get(
            "section_heading", item.get("sectionHeading", item.get("section"))
        )
        excerpt = item.get(
            "source_excerpt", item.get("sourceExcerpt", item.get("excerpt"))
        )
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


# ---------------------------------------------------------------------------
# Semantic classification (P5): what KIND of clause is this?
# ---------------------------------------------------------------------------

_PROHIBITION_MARKERS = (
    "shall not",
    "must not",
    "cannot ",
    "can not ",
    "not permitted",
    "not allowed",
    "prohibit",
    "debar",
    "no bidder",
    "bids will be rejected",
    "bid will be rejected",
    "liable for rejection",
)
_EXEMPTION_MARKERS = (
    "exempt",
    "relaxation",
    "waiver",
    "waived",
    "not applicable to",
    "startup",
    "mse",
    "micro and small",
    "shall be relaxed",
)
_BUYER_DISCRETION_MARKERS = (
    "buyer may",
    "buyer can",
    "at the discretion",
    "reserves the right",
    "reserve the right",
    "option clause",
    "buyer reserves",
    "sole discretion",
    "decision of the buyer",
)
_ELIGIBILITY_MARKERS = (
    "eligib",
    "minimum",
    "at least",
    "criteria",
    "past experience",
    "average annual turnover",
    "required experience",
    "pre-qualif",
    "must possess",
    "should possess",
)
_RESTRICTION_MARKERS = (
    "only ",
    "restricted",
    "limitation",
    "limited to",
    "exclusively",
    "no more than",
    "subject to",
)
_CONTRACTUAL_MARKERS = (
    "contract",
    "warranty",
    "penalty",
    "penalties",
    "payment",
    "sdac",
    "agreement",
    "extension",
    "option clause",
)
_OBLIGATION_MARKERS = (
    "shall",
    "must",
    "mandatory",
    "required",
    "should ",
    "upload",
    "submit",
    "provide",
    "ensure",
    "furnish",
    "enclose",
)
_INFORMATIONAL_MARKERS = (
    "click here",
    "uploaded by",
    "buyer uploaded",
    "uploaded ",
    "for reference",
    "details below",
    "file attachment",
    "annex",
    "enclosed",
    "for information",
)
_CONDITIONAL_MARKERS = (
    "if ",
    "only if",
    "applicable",
    "conditional",
    "subject to",
    "in case",
    "where ",
    "provided that",
    "requires consent",
    "consent of",
)


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(m in text for m in markers)


def classify_semantics(
    title: str, original_text: str, model_value: Any = None
) -> tuple[str, bool]:
    """Return (SemanticType value, strong_match).

    Deterministic keyword classification over title + original text. A
    model's suggestion is honoured only when no strong marker fires and the
    suggestion maps to a known type. Priority order matters: prohibitions and
    exemptions are checked before generic obligation wording so negations and
    relaxations are never flattened into "must do X".
    """
    from app.models.tender import SemanticType

    valid = {t.value for t in SemanticType}
    text = f"{title or ''} {original_text or ''}".lower()

    model_mapped: str | None = None
    if model_value:
        cand = str(model_value).strip().upper().replace(" ", "_").replace("-", "_")
        if cand in valid:
            model_mapped = cand

    if _has_any(text, _PROHIBITION_MARKERS):
        return SemanticType.PROHIBITION.value, True
    if _has_any(text, _EXEMPTION_MARKERS):
        return SemanticType.EXEMPTION.value, True
    if _has_any(text, _BUYER_DISCRETION_MARKERS):
        return SemanticType.BUYER_DISCRETION.value, True
    if _has_any(text, _ELIGIBILITY_MARKERS):
        return SemanticType.ELIGIBILITY_CONDITION.value, True
    if _has_any(text, _RESTRICTION_MARKERS):
        return SemanticType.RESTRICTION.value, True
    if _has_any(text, _CONTRACTUAL_MARKERS):
        return SemanticType.CONTRACTUAL_TERM.value, True
    if _has_any(text, _INFORMATIONAL_MARKERS):
        return SemanticType.INFORMATIONAL.value, True
    if model_mapped:
        return model_mapped, False
    if _has_any(text, _OBLIGATION_MARKERS):
        return SemanticType.BIDDER_OBLIGATION.value, True
    return SemanticType.INFORMATIONAL.value, False


def refine_mandatory(
    mandatory: bool | None,
    condition: str | None,
    title: str,
    original_text: str,
) -> tuple[bool | None, str | None]:
    """Conservative mandatory post-processing (P5).

    Explicit model values are never flipped. Only None is resolved, using
    strong wording signals — and conditional wording always wins over
    obligation wording so "must ... if ..." stays conditional.
    Returns (mandatory, condition) with condition possibly enriched.
    """
    if mandatory is not None:
        return mandatory, condition
    text = f"{title or ''} {original_text or ''} {condition or ''}".lower()
    if _has_any(text, _CONDITIONAL_MARKERS):
        return None, condition
    if _has_any(text, _PROHIBITION_MARKERS):
        return True, condition
    # Informational / buyer-discretion wording beats generic obligation
    # verbs: "buyer uploaded ..." contains "upload" but is no bidder duty.
    if _has_any(text, _BUYER_DISCRETION_MARKERS) or _has_any(
        text, _INFORMATIONAL_MARKERS
    ):
        return False, condition
    if _has_any(text, _OBLIGATION_MARKERS):
        return True, condition
    return None, condition


# ---------------------------------------------------------------------------
# Title equivalence (P1-contradiction): compare only semantically equivalent
# requirements, never everything sharing a category+unit.
# ---------------------------------------------------------------------------

_EQUIV_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "for",
        "and",
        "to",
        "in",
        "on",
        "by",
        "bidder",
        "bid",
        "bids",
        "requirement",
        "requirements",
        "details",
        "detail",
        "clause",
        "service",
        "services",
        "tender",
        "gem",
    }
)


def _content_tokens(title: str) -> frozenset[str]:
    return frozenset(
        t
        for t in re.findall(r"[a-z0-9]+", title.lower())
        if t not in _EQUIV_STOPWORDS and len(t) > 1
    )


def titles_equivalent(t1: str, t2: str, threshold: float = 0.5) -> bool:
    """True when two titles plausibly denote the same measure.

    Token overlap over stopword-filtered content words; identical topic keys
    always match. 'Minimum average annual turnover' vs 'Estimated bid value'
    scores 0 and is never compared; 'Minimum turnover' vs 'Bidder turnover
    requirement' shares 'turnover' and is compared.
    """
    if not t1 or not t2:
        return False
    a, b = _content_tokens(t1), _content_tokens(t2)
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= threshold


def _enum_value(value: Any) -> str:
    """Display-safe category/semantic value.

    model_dump() keeps classic str-Enum members, and str() of those renders
    as "RequirementCategory.X" — so unwrap Enum before the str check.
    """
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, str):
        return value
    return str(value)


# ---------------------------------------------------------------------------
# Normalized descriptions (P3): deterministic, restating, never inventing.
# ---------------------------------------------------------------------------

_SEMANTIC_PREFIX = {
    "BIDDER_OBLIGATION": "Bidder must",
    "ELIGIBILITY_CONDITION": "Eligibility",
    "EXEMPTION": "Exemption",
    "BUYER_DISCRETION": "Buyer may",
    "PROHIBITION": "Prohibition",
    "RESTRICTION": "Restriction",
    "CONTRACTUAL_TERM": "Contract term",
    "INFORMATIONAL": "Note",
}

_OPERATOR_PHRASE = {
    ">=": "at least",
    "<=": "at most",
    "=": "exactly",
    "~": "approximately",
}


def fmt_number(value: float) -> str:
    """Human-readable number for descriptions: 50000000 -> "50,000,000"."""
    try:
        if float(value).is_integer():
            return f"{int(value):,}"
        return f"{round(float(value), 2):,}"
    except (ValueError, TypeError):
        return str(value)


def compose_normalized_description(
    *,
    title: str,
    mandatory: bool | None,
    threshold: dict[str, Any] | None,
    condition: str | None,
    evidence: list[str],
    semantic_type: str,
    deadlines: list[str] | None = None,
    original: str = "",
) -> tuple[str | None, str | None]:
    """Build normalized_description from parsed fields only.

    Returns (description_or_None, review_flag_or_None). Every branch restates
    extracted facts behind an accurate semantic label — deadlines read as
    constraints/dates (never generic bidder actions), informational figures
    as reference figures (never binding thresholds), prohibitions keep their
    negation via the source wording, and bare "Prefix: Title." tautologies
    gain source detail instead. Nothing is invented: when no reliable
    interpretation exists the description stays None with a flag.
    """
    deadlines = deadlines or []
    generic_titles = {"", "requirement", "note", "tender requirement", "information"}
    clean_title = title.strip() if title else ""
    has_title = bool(clean_title and clean_title.lower() not in generic_titles)
    has_threshold = bool(threshold and threshold.get("value") is not None)
    has_substance = bool(
        condition or evidence or deadlines or has_threshold or mandatory is not None
    )
    if not has_title or not has_substance:
        return (
            None,
            "Normalized description unavailable — meaning cannot be established reliably",
        )

    # Deadline-driven clauses: constraints or dates, not generic actions.
    # An explicit mandatory=True is a submission constraint even when the
    # wording looks informational; mandatory=False (or undecided
    # informational) is a date, not a duty.
    if deadlines:
        date = deadlines[0]
        if mandatory is False or (
            mandatory is None and semantic_type == "INFORMATIONAL"
        ):
            head = f"Informational date: {clean_title} — {date}."
        else:
            head = f"Submission constraint: {clean_title} — {date}."
        tail = ""
        if condition and date not in condition:
            tail += f" Applies when: {condition.strip()}."
        if evidence:
            tail += " Provide: " + "; ".join(evidence) + "."
        return head + tail, None

    prefix = _SEMANTIC_PREFIX.get(semantic_type, "Requirement")
    # Informational figures are reference data, never binding thresholds.
    if semantic_type == "INFORMATIONAL" and has_threshold:
        assert threshold is not None  # guaranteed by has_threshold above
        unit = f" {threshold['unit']}" if threshold.get("unit") else ""
        figure = f"{fmt_number(threshold['value'])}{unit}"
        desc = f"Reference figure: {clean_title} — {figure}"
        verbatim = (threshold.get("original_value", "") or "").strip()
        # Skip the parenthetical when it merely echoes the formatted figure.
        if verbatim and verbatim.lower() != figure.lower():
            desc += f" ({verbatim})"
        desc += "."
        if evidence:
            desc += " Provide: " + "; ".join(evidence) + "."
        return desc, None

    parts = [f"{prefix}: {clean_title}."]
    if has_threshold:
        assert threshold is not None  # guaranteed by has_threshold above
        op = _OPERATOR_PHRASE.get(threshold.get("operator", "="), "exactly")
        unit = f" {threshold['unit']}" if threshold.get("unit") else ""
        parts.append(f"Threshold: {op} {fmt_number(threshold['value'])}{unit}.")
    elif threshold is not None:
        # Qualitative threshold with verbatim wording but no number.
        parts.append(f"Threshold (unquantified): {threshold.get('original_value')}.")
    if condition:
        parts.append(f"Applies when: {condition.strip()}.")
    elif mandatory is False:
        parts.append("Not mandatory.")
    if evidence:
        parts.append("Provide: " + "; ".join(evidence) + ".")
    # Prohibitions carry their negation in the source wording — restate it so
    # "Prohibition: <label>." never stands alone without the actual ban.
    if semantic_type == "PROHIBITION" and original:
        snippet = _snippet(original)
        if (
            snippet.lower() not in clean_title.lower()
            and clean_title.lower() not in snippet.lower()
        ):
            parts.append(f"Details: {snippet}.")
    # Bare "Prefix: Title." adds nothing over the title — ground it with the
    # source excerpt instead of emitting a tautology.
    if len(parts) == 1 and original:
        snippet = _snippet(original)
        if snippet and snippet.lower() != clean_title.lower():
            parts.append(f"Details: {snippet}.")
    return " ".join(parts), None


def _snippet(original: str, limit: int = 220) -> str:
    """Truncate source wording at a word boundary for description detail."""
    text = re.sub(r"\s+", " ", original.strip())
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return text[: cut if cut > limit // 2 else limit]


def normalize_metadata(data: Any) -> dict[str, Any]:
    """Lenient LLM metadata -> strict TenderMetadata dict (never raises on extras)."""
    if isinstance(data, BaseModel):
        raw = data.model_dump()
    elif hasattr(data, "model_dump") and callable(data.model_dump):
        try:
            dumped = data.model_dump()
            raw = dict(dumped) if isinstance(dumped, dict) else {}
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
    # Promote title-like keys hiding in other_metadata (P5): the model
    # sometimes files the title under a variant key instead of tender_title.
    if not clean.get("tender_title") and isinstance(clean.get("other_metadata"), dict):
        for key in (
            "tender_title",
            "title",
            "work_title",
            "project_title",
            "tender_name",
            "name_of_work",
            "work_name",
        ):
            candidate = clean["other_metadata"].get(key)
            if isinstance(candidate, str) and candidate.strip():
                clean["tender_title"] = _clean(candidate.strip(), 2000)
                break
    if not isinstance(clean.get("other_metadata"), dict):
        # Keep stray keys instead of dropping them.
        extras = {k: v for k, v in raw.items() if k not in allowed}
        clean["other_metadata"] = extras if isinstance(extras, dict) else {}
    return TenderMetadata(**clean).model_dump()


def _simple_to_canonical(d: dict[str, Any]) -> dict[str, Any]:
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


# Bid details (dates, reference numbers, estimates) are INFORMATION, not
# document asks: a date never requires proof from the bidder. Veto words
# are document-demand verbs/nouns — anything carrying them is a real ask.
_DOC_DEMAND_MARKERS = (
    "upload",
    "submit",
    "enclose",
    "furnish",
    "provide",
    "certificate",
    "evidence",
    "document",
    "attested",
    "self-certified",
)
_DETAIL_MARKERS = (
    "bid end",
    "bid open",
    "opening date",
    "end date",
    "deadline",
    "reference",
    "tender no",
    "tender number",
    "bid no",
    "tender id",
    "estimated",
    "validity",
    "published",
    "bid details",
    "version",
)
_ANY_DATE_RE = re.compile(
    r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{4}-\d{1,2}-\d{1,2}|"
    r"\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{2,4}",
    re.IGNORECASE,
)


def _is_informational_detail(title: str, original: str, semantic_type: str) -> bool:
    """True for bare bid details (dates, refs, estimates): never a document ask."""
    if semantic_type == "INFORMATIONAL":
        return True
    text = f"{title or ''} {original or ''}".lower()
    if _has_any(text, _DOC_DEMAND_MARKERS):
        return False
    if _ANY_DATE_RE.search(text):
        return True
    return _has_any(text, _DETAIL_MARKERS)


def is_proof_free_detail(req: dict[str, Any]) -> bool:
    """True for tender details/specs masquerading as requirements.

    A requirement is only real if the bidder must prove something: a
    supporting document (required_evidence) or a measurable company figure
    (numeric threshold). INFORMATIONAL clauses with neither — bid start/end
    dates, reference numbers, estimates, validity periods, portal boilerplate —
    are tender details, not requirements, and must not reach evidence mapping,
    compliance, or blockers. Evidence-bearing INFORMATIONAL clauses (a real
    document ask the classifier left as INFORMATIONAL) are kept.
    """
    if _enum_value(req.get("semantic_type")) != "INFORMATIONAL":
        return False
    return not req.get("required_evidence")


# ---------------------------------------------------------------------------
# Referenced-attachment detection (P4/P6): buyer-side documents cited in the
# tender (ATC, SLA, annexures...) that are NOT part of the analyzed PDF.
# Distinct from required_evidence (documents the *bidder* must submit).
# ---------------------------------------------------------------------------

_ATTACHMENT_PATTERNS = (
    (r"\batc\b", "ATC document"),
    (r"\bsla\b", "SLA document"),
    (r"annexures?", "annexure"),
    (r"appendix|appendices", "appendix"),
    (r"addendum|addenda", "addendum"),
    (r"corrigendum|corrigenda", "corrigendum"),
    (r"amendments?", "amendment"),
    (r"enclosures?", "enclosure"),
    (r"click here to view", "linked file (click here to view)"),
    (r"file attachments?", "file attachment"),
    (r"buyer added bid specific", "buyer-added bid-specific document"),
)


def find_attachment_refs(*texts: Any) -> list[str]:
    """Detect references to annexures/ATC/SLA/attachments in free text.

    Returns short display labels, longest-match-wins per region (so
    "annexure" does not also report "annex"). Word boundaries guard short
    tokens like ATC/SLA against mid-word matches.
    """
    blob = " ".join(str(t) for t in texts if t).lower()
    found: list[str] = []
    for pattern, label in _ATTACHMENT_PATTERNS:
        if re.search(pattern, blob) and label not in found:
            found.append(label)
    return found


def _clean_strict(d: dict[str, Any]) -> dict[str, Any]:
    """Sanitize strings inside an already-strict requirement dict (one level)."""
    out = dict(d)
    for k, v in out.items():
        if isinstance(v, str):
            out[k] = _clean(v, 4000)
        elif isinstance(v, list):
            out[k] = [
                {
                    kk: (_clean(vv, 2000) if isinstance(vv, str) else vv)
                    for kk, vv in i.items()
                }
                if isinstance(i, dict)
                else (_clean(i, 1000) if isinstance(i, str) else i)
                for i in v
            ]
        elif isinstance(v, dict):
            out[k] = {
                kk: (_clean(vv, 2000) if isinstance(vv, str) else vv)
                for kk, vv in v.items()
            }
    return out


def normalize_requirement(
    item: Any,
    document_name: str,
    default_page: int = 1,
    index: int = 0,
) -> dict[str, Any]:
    """Lenient LLM requirement (model | dict | mock) -> strict TenderRequirement dict.

    Pipeline per requirement: extract raw fields -> infer threshold operator
    with title/original context -> conservative mandatory resolution ->
    semantic classification -> deterministic normalized description ->
    specific review reasons. Serious gaps (empty source text, unparseable
    thresholds, missing excerpts) always set needs_review with an
    explanation — they can never pass silently.
    """
    if isinstance(item, LLMSimpleRequirement):
        # Flat primary shape: remap to canonical keys, then run the same path.
        item = _simple_to_canonical(item.model_dump())
    if (
        hasattr(item, "model_dump")
        and callable(item.model_dump)
        and not isinstance(item, LLMRequirement)
    ):
        # Already-strict shapes (e.g. test mocks whose model_dump returns a
        # strict dict): validate directly, sanitizing strings on the way.
        try:
            dumped = item.model_dump()
            strict_in = dumped if isinstance(dumped, dict) else {}
            return _clean_strict(TenderRequirement(**strict_in).model_dump())
        except Exception:
            try:
                dumped = item.model_dump()
                raw = dict(dumped) if isinstance(dumped, dict) else {}
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
    applicability_parts = [p for p in [condition, extra_condition] if p]
    if notes:
        applicability_parts.extend(_as_str_list(notes))
    # A bare deadline ("Deadlines: ...") is informative, not conditional —
    # only genuine condition text marks the requirement conditional (P6).
    has_real_condition = bool(applicability_parts)
    if deadlines:
        applicability_parts.append("Deadlines: " + "; ".join(deadlines))
    applicability = "; ".join(applicability_parts) or None
    # Conservative resolution of undecided mandatory (explicit values win).
    mandatory, applicability = refine_mandatory(
        mandatory, applicability, str(title), str(original)
    )

    threshold_raw = _pick("threshold")
    if threshold_raw is None:
        threshold_raw = _pick("thresholds")
    threshold, threshold_flag = parse_threshold(
        threshold_raw, context=f"{title} {original}"
    )

    evidence = _as_str_list(_pick("required_evidence", "evidence"))

    semantic_type, _ = classify_semantics(
        str(title), str(original), model_value=_pick("semantic_type")
    )

    # Bare bid details (dates, refs, estimates) are INFORMATION, never a
    # document ask: the model sometimes demands "Bid document" proof for a
    # date. Clear it — a date requires no bidder paperwork.
    if _is_informational_detail(str(title), str(original), semantic_type):
        semantic_type = "INFORMATIONAL"
        evidence = []

    # Exemptions/relaxations are relief from a base requirement — never an
    # ordinary threshold the bidder must satisfy (P2). Keep the verbatim
    # wording and unit, but drop the numeric obligation framing; the base
    # link is attached later by link_exemptions().
    if semantic_type == "EXEMPTION" and threshold is not None:
        threshold, threshold_flag = _reframe_exemption_threshold(
            threshold, str(original), applicability, threshold_flag
        )

    excerpt_fallback = str(original)[:2000]
    refs_raw = _pick("source_references", "sources", "references")
    refs: list[dict[str, Any]] = []
    if isinstance(refs_raw, list) and refs_raw:
        flat_page = _pick("page", "page_number", "pageNumber")
        try:
            default_ref_page = _coerce_int(flat_page, default_page)
        except Exception:
            default_ref_page = default_page
        for r in refs_raw:
            refs.append(
                _normalize_source_ref(
                    r, document_name, default_ref_page, excerpt_fallback
                )
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
    model_flagged = needs_review_raw is True or (
        isinstance(needs_review_raw, str)
        and needs_review_raw.strip().lower() in ("true", "yes", "1")
    )
    review_reason = _pick("review_reason")
    synthesized_id = not _pick("requirement_id", "requirementId", "id")

    composed_description, description_flag = compose_normalized_description(
        title=str(title),
        mandatory=mandatory,
        threshold=threshold,
        condition=applicability,
        evidence=evidence,
        semantic_type=semantic_type,
        deadlines=deadlines,
        original=str(original),
    )
    # Prefer the deterministic composition; fall back to a model-supplied
    # description only when composition is impossible (still flagged below).
    normalized_description = composed_description
    if normalized_description is None:
        model_description = _pick("normalized_description", "normalizedDescription")
        if isinstance(model_description, str) and model_description.strip():
            normalized_description = model_description.strip()

    # Specific, actionable review reasons (P6) — generic one-liners removed.
    reasons: list[str] = []
    if synthesized_id:
        reasons.append(
            f"Requirement ID auto-generated as '{req_id}' (model omitted requirement_id)"
        )
    if threshold_flag:
        reasons.append(threshold_flag)
    if mandatory is None and has_real_condition:
        cond_text = applicability or ""
        cond = cond_text if len(cond_text) <= 200 else cond_text[:200] + "..."
        reasons.append(f"Conditional requirement — applies when: {cond}")
    attachments = find_attachment_refs(title, original, evidence, applicability)
    if attachments:
        reasons.append(
            "References unavailable attachment: " + ", ".join(attachments[:3])
        )
    has_excerpt = any((r.get("source_excerpt") or "").strip() for r in refs)
    if not refs or not has_excerpt:
        reasons.append("No source excerpt captured — origin page unverified")
    if not str(original).strip():
        reasons.append("Empty source text — extraction unverified")
    if description_flag:
        reasons.append(description_flag)
    category_value = parse_category(_pick("category"))
    if category_value == RequirementCategory.OTHER.value:
        reasons.append("Topic category defaulted to OTHER — verify classification")
    if model_flagged and not reasons:
        reasons.append("Flagged by extraction model (no specific reason given)")
    if (
        isinstance(review_reason, str)
        and review_reason.strip()
        and review_reason.strip() not in reasons
    ):
        reasons.append(review_reason.strip())
    needs_review = bool(reasons) or model_flagged

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

    strict: dict[str, Any] = {
        "requirement_id": _clean(str(req_id), 100),
        "category": category_value,
        "semantic_type": semantic_type,
        "title": _clean(str(title), 500),
        "original_text": _clean(str(original), 4000) or _clean(str(title), 4000),
        "normalized_description": _clean(normalized_description, 2000),
        "mandatory": mandatory,
        "applicability_condition": _clean(applicability, 2000),
        "threshold": threshold,
        "required_evidence": [_clean(e, 1000) for e in evidence],
        "source_references": refs,
        "depends_on": [_clean(d, 200) for d in depends],
        "confidence": confidence,
        "needs_review": needs_review,
        "review_reason": _clean("; ".join(reasons) or None, 1000),
    }
    return TenderRequirement(**strict).model_dump()


def normalize_requirement_list(
    data: Any, document_name: str, default_page: int = 1, start_index: int = 0
) -> list[dict[str, Any]]:
    """Normalize one chunk's requirements.

    start_index is the global running offset so auto-generated IDs
    (R-{n}) never restart numbering per chunk (P1).
    """
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
    out: list[dict[str, Any]] = []
    dropped_details = 0
    for idx, item in enumerate(items):
        try:
            norm = normalize_requirement(
                item, document_name, default_page, start_index + idx
            )
        except Exception as e:
            logger.debug("Skipping unrecoverable requirement: %s", e, exc_info=True)
            continue  # skip unrecoverable items, keep the rest
        # Tender details (dates, refs, estimates) need no bidder proof:
        # drop them so they never reach evidence mapping or blockers.
        if is_proof_free_detail(norm):
            dropped_details += 1
            logger.debug("Dropping proof-free tender detail: %r", norm.get("title"))
            continue
        out.append(norm)
    if dropped_details:
        logger.info("Dropped %d proof-free tender detail(s)", dropped_details)
    return out


def _dedup_key(req: dict[str, Any]) -> str:
    """Identity key for cross-chunk duplicates: normalized title + excerpt head."""
    title = re.sub(r"\s+", " ", str(req.get("title", "")).strip().lower())
    excerpt = re.sub(r"\s+", " ", str(req.get("original_text", "")).strip().lower())[
        :200
    ]
    return f"{req.get('category', '')}||{title}||{excerpt}"


def deduplicate_requirements(
    requirements: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Drop verbatim cross-chunk duplicates, keeping the FIRST occurrence.

    Stable IDs are preserved: the kept requirement retains its ID; dropped
    duplicate IDs are returned for reporting. Order is preserved.
    """
    seen: dict[str, dict[str, Any]] = {}
    dropped: list[str] = []
    for req in requirements:
        key = _dedup_key(req)
        if key in seen:
            dropped.append(str(req.get("requirement_id", "?")))
            continue
        seen[key] = req
    return list(seen.values()), dropped


def ensure_unique_ids(
    requirements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Guarantee globally unique requirement IDs (P1).

    The first occurrence of an ID is stable and untouched; later collisions
    (the model restarts R1..Rn every chunk) get deterministic "-2", "-3", ...
    suffixes in encounter order.
    """
    seen: dict[str, int] = {}
    for req in requirements:
        base = str(req.get("requirement_id") or "R-?")
        if base not in seen:
            seen[base] = 1
            req["requirement_id"] = base
        else:
            seen[base] += 1
            req["requirement_id"] = f"{base}-{seen[base]}"
    return requirements
