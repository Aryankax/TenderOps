"""Lenient LLM-side schemas for bid drafting (flat scalars only)."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.aliases import AliasChoices

from app.agents.evidence_agent.verification import coerce_computed
from app.core.sanitize import sanitize_text
from app.models.bid import BidDraft

_REF_RE = re.compile(r"^(?P<doc>.+?)\s+p\.?\s*(?P<page>\d+)\s*$", re.IGNORECASE)


class LenientBase(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class LLMDraft(LenientBase):
    requirement_id: Any | None = Field(
        default=None,
        validation_alias=AliasChoices("requirement_id", "requirementId", "id"),
    )
    kind: Any | None = None
    title: Any | None = None
    body: Any | None = None
    evidence_refs: Any | None = Field(
        default=None,
        validation_alias=AliasChoices("evidence_refs", "evidenceRefs", "references"),
    )
    claimed_value: Any | None = None
    claimed_unit: Any | None = None
    needs_human_signoff: Any | None = Field(
        default=None,
        validation_alias=AliasChoices("needs_human_signoff", "needsSignoff"),
    )


class LLMDraftList(LenientBase):
    drafts: list[LLMDraft] = Field(default_factory=list)


_VALID_KINDS = {"requirement_response", "declaration", "annexure_stub"}


def _parse_refs(value: Any) -> list[dict[str, Any]]:
    """"Doc.pdf p.2" strings -> [{document_name, page_number}]."""
    refs: list[dict[str, Any]] = []
    items = value if isinstance(value, list) else [value]
    for item in items:
        if isinstance(item, dict) and item.get("document_name"):
            try:
                refs.append({
                    "document_name": str(item["document_name"]),
                    "page_number": int(item.get("page_number", 0) or 0),
                })
            except (ValueError, TypeError):
                continue
        elif isinstance(item, str) and item.strip():
            match = _REF_RE.match(item.strip())
            if match:
                refs.append({
                    "document_name": match.group("doc").strip(),
                    "page_number": int(match.group("page")),
                })
    return refs


def normalize_draft(item: Any, requirement_id: str, default_kind: str) -> dict[str, Any]:
    """Lenient draft -> strict BidDraft dict. Never raises for bad items."""
    if isinstance(item, BaseModel):
        raw = item.model_dump()
    elif isinstance(item, dict):
        raw = dict(item)
    else:
        raw = {}
    kind = str(raw.get("kind") or default_kind or "requirement_response").strip()
    if kind not in _VALID_KINDS:
        kind = "requirement_response"
    signoff = raw.get("needs_human_signoff")
    if not isinstance(signoff, bool):
        signoff = kind == "declaration"
    title = str(raw.get("title") or "").strip() or f"Draft for {requirement_id}"
    strict: dict[str, Any] = {
        "draft_id": f"D-{requirement_id}",
        "requirement_id": requirement_id,
        "kind": kind,
        "title": sanitize_text(title)[:300],
        "body": sanitize_text(str(raw.get("body") or ""))[:4000],
        "evidence_refs": _parse_refs(raw.get("evidence_refs")),
        "claimed_value": coerce_computed(raw.get("claimed_value")),
        "claimed_unit": (
            sanitize_text(str(raw["claimed_unit"]))[:60]
            if raw.get("claimed_unit") else None
        ),
        "needs_human_signoff": signoff,
    }
    return BidDraft(**strict).model_dump()
