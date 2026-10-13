"""Human-readable requirement references.

LLM-written free text (review reasons, evidence notes, blocker and issue
descriptions) cites requirements by bare ID ("R4 states ... but R7-2 ..."),
forcing reviewers to keep a lookup table open. This module expands those
citations into title + short description.

Structured ID fields (affected_requirement_ids, depends_on, requirement_id)
are deliberately untouched — only prose is rewritten.

Idempotency: expansions render the ID inside square brackets ("Title"
[R4]), and the matcher refuses bracketed IDs, so repeated passes never
re-expand. Unknown IDs are left as-is.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

# R1 / R1-2 (greedy tail first) / REQ-001 / ISSUE-001 / BLK-001 — but never
# an ID already expanded inside square brackets.
_ID_RE = re.compile(
    r"(?<!\[)\b(?:REQ-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*|R\d+(?:-\d+)?|ISSUE-\d+|BLK-\d+)\b(?!\])"
)

_DESC_CLIP = 100


def _clip(text: str, limit: int = _DESC_CLIP) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return text[: cut if cut > limit // 2 else limit].rstrip(" .,:;") + "…"


def build_ref_map(
    requirements: Sequence[Mapping[str, Any]] | None = None,
    issues: Sequence[Mapping[str, Any]] | None = None,
    blockers: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, str]:
    """Map every known ID to its human-readable expansion."""
    ref_map: dict[str, str] = {}
    for req in requirements or []:
        rid = str(req.get("requirement_id", "") or "").strip()
        if not rid:
            continue
        title = str(req.get("title", "") or "").strip() or rid
        desc = _clip(str(req.get("normalized_description", "") or ""))
        if desc and desc.lower() != title.lower():
            ref_map[rid] = f'"{title}" [{rid}] — {desc}'
        else:
            ref_map[rid] = f'"{title}" [{rid}]'
    for issue in issues or []:
        iid = str(issue.get("issue_id", "") or "").strip()
        if not iid or iid in ref_map:
            continue
        desc = _clip(str(issue.get("description", "") or ""), 80)
        label = str(issue.get("issue_type", "") or "issue").replace("_", " ").title()
        ref_map[iid] = f'"{label}" [{iid}]' + (f" — {desc}" if desc else "")
    for blocker in blockers or []:
        bid = str(blocker.get("blocker_id", "") or "").strip()
        if not bid or bid in ref_map:
            continue
        btype = (
            str(blocker.get("blocker_type", "") or "blocker").replace("_", " ").title()
        )
        ref_map[bid] = f'"{btype}" [{bid}]'
    return ref_map


def resolve_requirement_refs(
    text: str | None, ref_map: Mapping[str, str]
) -> str | None:
    """Replace bare IDs in prose with title+description expansions."""
    if not text or not ref_map:
        return text

    def _expand(match: re.Match[str]) -> str:
        rid = match.group(0)
        return ref_map.get(rid, rid)

    return _ID_RE.sub(_expand, text)
