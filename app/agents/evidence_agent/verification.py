"""Deterministic numeric verification of requirements against evidence.

The mapping LLM is forbidden from doing arithmetic, so a dedicated engine
does it instead: it reads the company's stated figures out of retrieved
excerpts, normalizes units (lakh/crore scaling included), and compares them
against the requirement's threshold operator (``>=`` / ``<=`` / ``=`` /
``~``). Anything it cannot decide cleanly returns ``None`` and falls
through to LLM judgment — it never guesses.

Design rules:
- One unknown never becomes another number: unparseable thresholds,
  incompatible units, and date-like figures all abstain (``None``).
- Informational figures (tender's own estimates) and buyer-discretion terms
  are not company targets — skipped, left to the LLM.
- Every verdict quotes both figures and names its source.
"""

from __future__ import annotations

import math
import re
from typing import Any

from app.agents.evidence_agent.retrieval import _content_tokens
from app.agents.tender_analyst.llm_schemas import (
    _enum_value,
    _extract_number,
    fmt_number,
)
from app.models.evidence import RequirementEvidence

# Requirement classes with no company-side satisfaction target. Exemptions
# are included defensively: post-reframe they carry value None anyway, but
# a relief must never be judged as an obligation even if a number survives.
_NON_VERIFIABLE_SEMANTICS = frozenset({"INFORMATIONAL", "BUYER_DISCRETION", "EXEMPTION"})

_UNIT_ALIASES = {    "rs": "inr",
    "rs.": "inr",
    "inr": "inr",
    "inr.": "inr",
    "rupee": "inr",
    "rupees": "inr",
    "re": "inr",
    "\u20b9": "inr",
    "day": "days",
    "days": "days",
    "year": "years",
    "years": "years",
    "month": "months",
    "months": "months",
    "bid": "bids",
    "bids": "bids",
}

_DATE_SPAN_RE = re.compile(
    r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{4}-\d{1,2}-\d{1,2}|\b\d{4}-\d{2}\b|"
    r"\d{1,2}:\d{2}(?::\d{2})?"
)

# Unit word immediately AFTER a number ("5 crore", "180 days", "7 Year(s)").
_UNIT_AFTER_RE = re.compile(
    r"^\s*(lakhs?|lacs?|crores?|inr\.?|rs\.?|rupees?|\u20b9|days?|years?|months?|"
    r"bids?|percent|%)(\(s\))?",
    re.IGNORECASE,
)
_SCALE_WORDS = {
    "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
    "crore": 1e7, "crores": 1e7,
}
_MONEY_WORDS = frozenset({
    "lakh", "lakhs", "lac", "lacs", "crore", "crores",
    "inr", "inr.", "rs", "rs.", "rupee", "rupees", "\u20b9",
})
# Currency marker BEFORE a bare number ("Rs. 9874275").
_MONEY_BEFORE_RE = re.compile(r"(rs\.?|inr|rupees?|\u20b9)\s*\.?\s*$", re.IGNORECASE)


def _unit_after(text: str, start: int, end: int) -> tuple[float, str]:
    """(scale, unit-word) around a number at text[start:end] (P2: prose-safe).

    Only a single adjacent unit word counts — unlike model-supplied strings,
    raw excerpts run on ("5 crore as per audited statements"), so grabbing
    the whole remainder would poison the unit. Currency may also precede
    the number ("Rs. 9874275").
    """
    match = _UNIT_AFTER_RE.match(text[end : end + 12])
    if match:
        word = match.group(1).lower().rstrip(".")
        scale = _SCALE_WORDS.get(word, 1.0)
        unit = "INR" if word in _MONEY_WORDS else word
        return scale, unit
    before = text[max(0, start - 8) : start]
    if _MONEY_BEFORE_RE.search(before):
        return 1.0, "INR"
    return 1.0, ""


def canon_unit(unit: Any) -> str:
    """Normalize a unit for comparison (plurals, INR spellings)."""
    text = str(unit or "").strip().lower().rstrip(".")
    text = re.sub(r"\(s\)$", "", text).strip()
    if text in _UNIT_ALIASES:
        return _UNIT_ALIASES[text]
    if len(text) > 4 and text.endswith("s") and not text.endswith("ss"):
        return text[:-1]
    return text


def _date_spans(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in _DATE_SPAN_RE.finditer(text)]


def extract_figures(text: str) -> list[dict[str, Any]]:
    """All (number, unit) figures in free text, dates excluded.

    Units come from a tight adjacent-word lexicon (plus currency-before-
    number), so prose after the figure never leaks into the unit. Values are
    lakh/crore pre-scaled, mirroring the analyst agent's normalization.
    """
    figures: list[dict[str, Any]] = []
    if not text:
        return figures
    spans = _date_spans(text)
    for match in re.finditer(r"[-+]?\d[\d,]*\.?\d*", text):
        start = match.start()
        if any(s <= start < e for s, e in spans):
            continue  # part of a date/time, not a figure
        number = _extract_number(match.group(0).replace(",", ""))
        if number is None:
            continue
        scale, unit = _unit_after(text, match.start(), match.end())
        figures.append(
            {
                "value": round(number * scale, 2),
                "unit": unit,
                "original": match.group(0),
                "pos": start,
            }
        )
    return figures


def _meets(operator: str, company: float, required: float) -> bool | None:
    """Operator-aware comparison with float-dust tolerance. None = unknown op."""
    eps = 1e-6 * max(1.0, abs(required))
    if operator == ">=":
        return company + eps >= required
    if operator == "<=":
        return company - eps <= required
    if operator == "=":
        return abs(company - required) <= max(eps, 0.01 * abs(required))
    if operator == "~":
        return abs(company - required) <= max(eps, 0.10 * abs(required))
    return None


def _topic_words(req: dict[str, Any]) -> set[str]:
    return _stem_tokens(
        _topic_tokens(
            f"{req.get('title', '')} {req.get('normalized_description', '')}"
        )
    )


# Retrieval's stopword list drops domain nouns ("bid", "turnover" context)
# that are noise across documents but signal within one excerpt — so topic
# matching uses this minimal glue-word set instead.
_TOPIC_STOPWORDS = frozenset({
    "the", "a", "an", "of", "for", "and", "to", "in", "on", "by",
    "with", "from", "is", "are", "was", "as", "at", "or", "that",
    "this", "it", "be", "an", "no", "not",
})


def _topic_tokens(text: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9]+", text.lower())
        if t not in _TOPIC_STOPWORDS and len(t) > 2
    }


def _clause_window(text: str, pos: int) -> str:
    """The sentence/clause containing position pos.

    Topic anchoring is clause-scoped, not excerpt-scoped: in "Project ...
    Timelines. Phase 1 completion in 7 days", the figure lives in a
    milestone clause, so a payment-timeline topic must NOT match via the
    distant word "Timelines". Periods inside abbreviations ("Rs.", "No.",
    "M/s") never split.
    """
    # Periods split only before capitals/end (never decimals, dates, or the
    # abbreviations below) — "Rs. 12 lakh" stays one clause.
    bound = (
        r"[!?;\n]+|(?<!\bRs)(?<!\bNo)(?<!\bM/s)(?<!\bFY)(?<!\be\.g)"
        r"(?<!\bi\.e)(?<!\bvs)(?<!\bst)\.(?=\s+[A-Z]|\s*$)"
    )
    bounds = [m.end() for m in re.finditer(bound, text[:pos])]
    start = bounds[-1] if bounds else 0
    tail = re.search(bound, text[pos:])
    end = pos + tail.start() if tail else len(text)
    return text[start:end]


def _stem(word: str) -> str:
    """Tiny plural-stemmer so 'bids' matches topic 'bid' (symmetric use)."""
    if len(word) > 4 and word.endswith("es"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s"):
        return word[:-1]
    return word


def _stem_tokens(tokens: set[str]) -> set[str]:
    return {_stem(t) for t in tokens}


def verify_numeric_requirement(
    req: dict[str, Any], hits: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Deterministically verify a numeric requirement. None = inconclusive.

    Picks the excerpt figure in a compatible unit, preferring figures near
    the requirement's topic words, and applies the threshold operator.
    """
    threshold = req.get("threshold") or {}
    required = threshold.get("value")
    operator = threshold.get("operator", "=")
    if required is None or operator == "unknown":
        return None
    if _enum_value(req.get("semantic_type")) in _NON_VERIFIABLE_SEMANTICS:
        return None
    wanted = canon_unit(threshold.get("unit", ""))
    if not wanted:
        return None
    topic = _topic_words(req)

    candidates: list[dict[str, Any]] = []
    for hit in hits:
        excerpt = str(hit.get("excerpt", ""))
        for fig in extract_figures(excerpt):
            if canon_unit(fig["unit"]) != wanted:
                continue
            window = _clause_window(excerpt, fig["pos"])
            near_topic = bool(topic & _stem_tokens(_topic_tokens(window)))
            candidates.append({"hit": hit, "fig": fig, "near_topic": near_topic})

    # A bare number+unit match is NOT enough: without topic anchoring, any
    # same-unit figure anywhere (a project plan's "7 days" vs the tender's
    # auto-extension parameter) would falsely verify. Abstain instead.
    topical = [c for c in candidates if c["near_topic"]]
    if not topical:
        return None
    if operator == ">=":
        best = max(topical, key=lambda c: c["fig"]["value"])
    elif operator == "<=":
        best = min(topical, key=lambda c: c["fig"]["value"])
    else:
        best = min(topical, key=lambda c: abs(c["fig"]["value"] - required))

    met = _meets(operator, best["fig"]["value"], required)
    if met is None:
        return None
    hit, fig = best["hit"], best["fig"]
    company_str = f"{fmt_number(fig['value'])} {fig['unit']}".strip()
    required_str = f"{operator} {fmt_number(required)} {threshold.get('unit', '')}".strip()
    source = f"{hit.get('document_name', '?')} p.{hit.get('page_number', '?')}"
    # Confidence blends engine certainty with retrieval quality: a perfect
    # reading off a 0.31-score hit is not a 0.9.
    try:
        hit_score = float(hit.get("score") or 0.5)
    except (ValueError, TypeError):
        hit_score = 0.5
    base_conf = 0.9 if len(topical) == 1 else 0.75
    confidence = round(min(base_conf, 0.55 + 0.45 * hit_score), 2)
    if met:
        return RequirementEvidence(
            requirement_id=str(req.get("requirement_id", "?")),
            requirement_title=str(req.get("title", "") or "") or None,
            status="satisfied",
            confidence=confidence,
            hits=hits,
            computed_value=fig["value"],
            computed_unit=fig["unit"] or None,
            notes=(
                f"Company figure {company_str} ({source}) meets required "
                f"{required_str}."
            ),
        ).model_dump()
    return RequirementEvidence(
        requirement_id=str(req.get("requirement_id", "?")),
        requirement_title=str(req.get("title", "") or "") or None,
        status="missing",
        confidence=round(min(0.85, 0.55 + 0.45 * hit_score), 2),
        hits=hits,
        computed_value=fig["value"],
        computed_unit=fig["unit"] or None,
        notes=(
            f"Company figure {company_str} ({source}) does NOT meet required "
            f"{required_str}."
        ),
    ).model_dump()


def coerce_computed(value: Any) -> float | None:
    """Parse an LLM-reported figure ("5 crore", "1156.37 Lakh", 42) to float."""
    try:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        try:
            return float(text.replace(",", ""))
        except ValueError:
            pass
        number = _extract_number(text)
        if number is None:
            return None
        low = text.lower()
        if re.search(r"\bcrore?s?\b", low):
            scale = 1e7
        elif re.search(r"\b(lakh|lac)s?\b", low):
            scale = 1e5
        else:
            scale = 1.0
        return round(number * scale, 2)
    except (ValueError, TypeError, AttributeError):
        return None


def audit_numeric_claim(
    req: dict[str, Any], status: str, computed_value: Any, notes: str | None
) -> tuple[str, float | None, str | None]:
    """Cross-check an LLM satisfied-claim against the threshold.

    Returns (status, confidence_adjustment_or_None, appended_note_or_None).
    A claim contradicted by its own quoted figure is downgraded to
    needs_review instead of passing silently; anything unparseable is left
    to the LLM's judgment.
    """
    threshold = req.get("threshold") or {}
    required = threshold.get("value")
    operator = threshold.get("operator", "=")
    if (
        status != "satisfied"
        or required is None
        or operator == "unknown"
        or _enum_value(req.get("semantic_type")) == "INFORMATIONAL"
    ):
        return status, None, None
    company = coerce_computed(computed_value)
    if company is None:
        return status, None, None
    met = _meets(operator, company, required)
    if met is True:
        return status, None, None
    if met is False:
        detail = (
            f"Audit: company figure {fmt_number(company)} does not meet required "
            f"{operator} {fmt_number(required)} "
            f"{threshold.get('unit', '')} — flagged for review."
        )
        combined = f"{notes} {detail}".strip() if notes else detail
        return "needs_review", 0.4, combined[:1000]
    return status, None, None
