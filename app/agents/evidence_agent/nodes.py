from typing import Any
import re

from app.agents.evidence_agent.llm_schemas import LLMMappingList, normalize_mapping
from app.agents.evidence_agent.verification import verify_numeric_requirement
from app.agents.evidence_agent.prompts import EVIDENCE_MAPPING_PROMPT
from app.agents.evidence_agent.retrieval import query_evidence
from app.agents.evidence_agent.state import EvidenceAgentState

# Shared tolerant structured-output caller (provider chain + retries).
# Imported from the analyst agent to avoid duplicating the logic.
from app.agents.tender_analyst.nodes import _invoke_structured
from app.core.config import settings
from app.core.requirement_refs import build_ref_map, resolve_requirement_refs
from app.services.observability import observability


def _requirement_query_text(req: dict[str, Any]) -> str:
    parts = [
        str(req.get("title", "")),
        str(req.get("normalized_description") or ""),
        str((req.get("threshold") or {}).get("original_value") or ""),
        " ".join(req.get("required_evidence") or []),
    ]
    return " ".join(p for p in parts if p.strip())[:1000]


def _requirement_queries(req: dict[str, Any]) -> list[str]:
    """Several query angles per requirement (local search is cheap).

    One vocabulary miss ("bid security" vs "EMD") must not hide a document:
    title-led, evidence-led, and measure-led variants cover the angles.
    """
    title = str(req.get("title", "")).strip()
    evidence = " ".join(req.get("required_evidence") or []).strip()
    threshold = str((req.get("threshold") or {}).get("original_value") or "").strip()
    variants = [
        _requirement_query_text(req),
        " ".join(p for p in [title, evidence] if p),
        " ".join(p for p in [title, threshold] if p),
    ]
    seen: list[str] = []
    for v in variants:
        v = v.strip()[:1000]
        if v and v not in seen:
            seen.append(v)
    return seen


def _merge_hits(hit_lists: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Union retrieval hits, deduped by (document, page), best score kept."""
    merged: dict[tuple[str, int], dict[str, Any]] = {}
    for hits in hit_lists:
        for h in hits:
            key = (str(h.get("document_name", "?")), int(h.get("page_number", 0) or 0))
            if key not in merged or float(h.get("score", 0) or 0) > float(
                merged[key].get("score", 0) or 0
            ):
                merged[key] = h
    return sorted(merged.values(), key=lambda h: float(h.get("score", 0) or 0), reverse=True)


def _select_documents(
    hits: list[dict[str, Any]], title: str, top_docs: int = 2
) -> list[str]:
    """Rank candidate documents: best hit score, then hit count, then a
    filename bonus when the file is literally named for the topic
    ("Turnover-Cert.pdf" for a turnover requirement). Explainable, no LLM."""
    title_words = {
        t for t in re.findall(r"[a-z0-9]+", title.lower()) if len(t) > 3
    }
    scored: dict[str, list[float]] = {}
    for h in hits:
        doc = str(h.get("document_name", "?"))
        try:
            score = float(h.get("score", 0) or 0)
        except (ValueError, TypeError):
            score = 0.0
        entry = scored.setdefault(doc, [0.0, 0])
        entry[0] = max(entry[0], score)
        entry[1] += 1
    ranked = []
    for doc, (best, count) in scored.items():
        bonus = 0.2 if title_words and any(
            w in doc.lower() for w in title_words
        ) else 0.0
        ranked.append((best + 0.05 * count + bonus, doc))
    ranked.sort(reverse=True)
    return [doc for _, doc in ranked[: max(1, top_docs)]]


def retrieve_evidence_node(state: EvidenceAgentState) -> EvidenceAgentState:
    tender_id = state.get("tender_id", "unknown")
    profile = state.get("company_profile_id", "sample")
    scoped_docs = [d for d in (state.get("source_documents") or []) if d]
    if scoped_docs:
        observability.log_event(
            "retrieve_scoped", tender_id=tender_id, documents=scoped_docs
        )
    else:
        observability.log_event("node_start", node="retrieve_evidence", tender_id=tender_id)
    retrieved: dict[str, list[dict[str, Any]]] = {}
    width = max(1, settings.EVIDENCE_RETRIEVE_WIDTH)
    cap = max(1, settings.EVIDENCE_MAX_EXCERPTS)
    for req in state.get("requirements", []):
        rid = str(req.get("requirement_id", "?"))
        try:
            queries = _requirement_queries(req)
            if scoped_docs:
                # Targeted re-check: search ONLY the fix documents attached
                # to this blocker — never the whole company index.
                merged = _merge_hits(
                    [
                        query_evidence(q, profile, top_k=width, document_name=doc)
                        for q in queries
                        for doc in scoped_docs
                    ]
                )
                if not merged:
                    # No gated hit — but the file must still be ANALYSED, not
                    # skipped: hand its top chunks to the mapping LLM as
                    # low-score context and let it judge. Scores stay honest,
                    # so verdicts come back low-confidence, never inflated.
                    merged = _merge_hits(
                        [
                            query_evidence(
                                q, profile, top_k=3, document_name=doc,
                                bypass_gates=True,
                            )
                            for q in queries
                            for doc in scoped_docs
                        ]
                    )[:3]
                    observability.log_event(
                        "retrieve_scoped_fallback", tender_id=tender_id,
                        requirement_id=rid, documents=scoped_docs,
                        fallback_chunks=len(merged),
                    )
            else:
                # 1. Broad recall across query angles, merged + deduped.
                merged = _merge_hits(
                    [query_evidence(q, profile, top_k=width) for q in queries]
                )
            # 2. Pick the right document(s), then pull fuller context from
            #    inside them — the decisive sentence may sit on another page
            #    of the same file.
            enriched = list(merged)
            seen = {
                (str(h.get("document_name", "?")), int(h.get("page_number", 0) or 0))
                for h in merged
            }
            for doc in _select_documents(merged, str(req.get("title", ""))):
                for h in query_evidence(
                    queries[0], profile, top_k=settings.EVIDENCE_TOP_K,
                    document_name=doc,
                ):
                    key = (
                        str(h.get("document_name", "?")),
                        int(h.get("page_number", 0) or 0),
                    )
                    if key not in seen:
                        seen.add(key)
                        enriched.append(h)
            enriched.sort(
                key=lambda h: float(h.get("score", 0) or 0), reverse=True
            )
            retrieved[rid] = enriched[:cap]
        except Exception as e:
            observability.log_error("retrieval_failed", e, requirement_id=rid)
            retrieved[rid] = []
            state.setdefault("warnings", []).append(
                f"Retrieval failed for {rid}; treated as missing evidence."
            )
    state["retrieved"] = retrieved
    with_hits = sum(1 for v in retrieved.values() if v)
    observability.log_event(
        "node_success",
        node="retrieve_evidence",
        tender_id=tender_id,
        requirements_with_hits=with_hits,
        total=len(retrieved),
    )
    return state


def _format_batch(
    batch: list[dict[str, Any]], retrieved: dict[str, list[dict[str, Any]]]
) -> str:
    blocks = []
    for req in batch:
        rid = str(req.get("requirement_id", "?"))
        lines = [
            f"Requirement {rid}: {req.get('title', '')}",
            f"  Details: {(req.get('normalized_description') or '')[:400]}",
        ]
        th = req.get("threshold") or {}
        if th.get("original_value"):
            lines.append(f"  Threshold: {th.get('original_value')}")
        if th.get("value") is not None:
            lines.append(
                f"  Target: {th.get('operator', '=')} {th.get('value')} "
                f"{th.get('unit', '')} (numeric test to apply)"
            )
        hits = retrieved.get(rid, [])
        if not hits:
            lines.append("  Evidence: NONE FOUND")
        else:
            by_doc: dict[str, list[dict[str, Any]]] = {}
            for h in hits:
                by_doc.setdefault(str(h.get("document_name", "?")), []).append(h)
            for doc, doc_hits in by_doc.items():
                lines.append(f"  Document: {doc} ({len(doc_hits)} excerpt(s)):")
                for h in doc_hits:
                    lines.append(
                        f"    [p.{h.get('page_number', '?')}"
                        f" score={h.get('score', '?')}]: {(h.get('excerpt') or '')[:600]}"
                    )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def map_evidence_node(state: EvidenceAgentState) -> EvidenceAgentState:
    tender_id = state.get("tender_id", "unknown")
    observability.log_event("node_start", node="map_evidence", tender_id=tender_id)
    requirements = state.get("requirements", [])
    retrieved = state.get("retrieved", {})
    titles = {
        str(r.get("requirement_id", "?")): str(r.get("title", "")) for r in requirements
    }
    items: list[dict[str, Any]] = []
    unmapped: list[str] = []
    batch_size = max(1, settings.EVIDENCE_MAP_BATCH_SIZE)

    # No-hit requirements skip the LLM entirely (budget + trivially missing).
    mappable = []
    for req in requirements:
        rid = str(req.get("requirement_id", "?"))
        if not retrieved.get(rid):
            items.append(
                normalize_mapping(
                    {
                        "status": "missing",
                        "confidence": 0.0,
                        "notes": "No company evidence retrieved.",
                    },
                    rid,
                    [],
                    titles.get(rid),
                    requirement=req,
                )
            )
            unmapped.append(rid)
            continue
        # Deterministic numeric verification first: exact, explainable, and
        # it saves an LLM call whenever the figures decide the matter.
        verdict = verify_numeric_requirement(req, retrieved[rid])
        if verdict is not None:
            items.append(verdict)
        else:
            mappable.append(req)

    for i in range(0, len(mappable), batch_size):
        batch = mappable[i : i + batch_size]
        prompt = EVIDENCE_MAPPING_PROMPT.format(
            batch_content=_format_batch(batch, retrieved)
        )
        try:
            res = _invoke_structured(
                LLMMappingList, prompt, operation=f"map_evidence_batch_{i}"
            )
            raw_list = getattr(res, "mappings", []) or []
            by_id = {}
            for m in raw_list:
                d = m.model_dump() if hasattr(m, "model_dump") else dict(m)
                key = str(
                    d.get("requirement_id")
                    or d.get("requirementId")
                    or d.get("id")
                    or ""
                )
                by_id[key] = d
            for req in batch:
                rid = str(req.get("requirement_id", "?"))
                items.append(
                    normalize_mapping(
                        by_id.get(rid, {}), rid, retrieved.get(rid, []), titles.get(rid),
                        requirement=req,
                    )
                )
        except Exception as e:
            observability.log_error("mapping_failed", e, batch_index=i)
            state.setdefault("warnings", []).append(
                f"Evidence mapping failed for batch starting at {i}; "
                f"marked needs_review."
            )
            for req in batch:
                rid = str(req.get("requirement_id", "?"))
                items.append(
                    normalize_mapping({}, rid, retrieved.get(rid, []), titles.get(rid),
                                      requirement=req)
                )

    # Preserve original requirement order.
    order = [str(r.get("requirement_id", "?")) for r in requirements]
    items.sort(
        key=lambda d: (
            order.index(d["requirement_id"])
            if d["requirement_id"] in order
            else len(order)
        )
    )
    state["items"] = items
    state["unmapped"] = unmapped
    observability.log_event(
        "node_success", node="map_evidence", tender_id=tender_id, mapped=len(items)
    )
    return state


def assemble_evidence_node(state: EvidenceAgentState) -> EvidenceAgentState:
    # Human-readable citations in mapping notes (bare IDs -> title + desc).
    ref_map = build_ref_map(state.get("requirements", []))
    for item in state.get("items", []):
        if item.get("notes"):
            item["notes"] = resolve_requirement_refs(item["notes"], ref_map)
    state["final_result"] = {
        "tender_id": state.get("tender_id", "unknown"),
        "company_profile_id": state.get("company_profile_id", "sample"),
        "items": state.get("items", []),
        "unmapped_requirements": state.get("unmapped", []),
        "warnings": state.get("warnings", []),
    }
    return state
