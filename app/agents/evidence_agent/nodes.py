from typing import Any, Dict, List

from app.agents.evidence_agent.state import EvidenceAgentState
from app.agents.evidence_agent.prompts import EVIDENCE_MAPPING_PROMPT
from app.agents.evidence_agent.llm_schemas import LLMMappingList, normalize_mapping
from app.agents.evidence_agent.retrieval import query_evidence
from app.core.config import settings
from app.services.observability import observability

# Shared tolerant structured-output caller (provider chain + retries).
# Imported from the analyst agent to avoid duplicating the logic.
from app.agents.tender_analyst.nodes import _invoke_structured


def _requirement_query_text(req: Dict[str, Any]) -> str:
    parts = [
        str(req.get("title", "")),
        str(req.get("normalized_description") or ""),
        str((req.get("threshold") or {}).get("original_value") or ""),
        " ".join(req.get("required_evidence") or []),
    ]
    return " ".join(p for p in parts if p.strip())[:1000]


def retrieve_evidence_node(state: EvidenceAgentState) -> EvidenceAgentState:
    tender_id = state.get("tender_id", "unknown")
    profile = state.get("company_profile_id", "sample")
    observability.log_event("node_start", node="retrieve_evidence", tender_id=tender_id)
    retrieved: Dict[str, List[Dict[str, Any]]] = {}
    for req in state.get("requirements", []):
        rid = str(req.get("requirement_id", "?"))
        try:
            retrieved[rid] = query_evidence(_requirement_query_text(req), profile)
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
    batch: List[Dict[str, Any]], retrieved: Dict[str, List[Dict[str, Any]]]
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
        hits = retrieved.get(rid, [])
        if not hits:
            lines.append("  Evidence: NONE FOUND")
        for h in hits[: settings.EVIDENCE_TOP_K]:
            lines.append(
                f"  Excerpt [{h['document_name']} p.{h['page_number']}"
                f" score={h['score']}]: {(h['excerpt'] or '')[:600]}"
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def map_evidence_node(state: EvidenceAgentState) -> EvidenceAgentState:
    tender_id = state.get("tender_id", "unknown")
    observability.log_event("node_start", node="map_evidence", tender_id=tender_id)
    requirements = state.get("requirements", [])
    retrieved = state.get("retrieved", {})
    items: List[Dict[str, Any]] = []
    unmapped: List[str] = []
    batch_size = max(1, settings.EVIDENCE_MAP_BATCH_SIZE)

    # No-hit requirements skip the LLM entirely (budget + trivially missing).
    mappable = []
    for req in requirements:
        rid = str(req.get("requirement_id", "?"))
        if not retrieved.get(rid):
            items.append(
                normalize_mapping({"status": "missing", "confidence": 0.0,
                                   "notes": "No company evidence retrieved."},
                                  rid, [])
            )
            unmapped.append(rid)
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
                key = str(d.get("requirement_id") or d.get("requirementId")
                          or d.get("id") or "")
                by_id[key] = d
            for req in batch:
                rid = str(req.get("requirement_id", "?"))
                items.append(
                    normalize_mapping(by_id.get(rid, {}), rid,
                                      retrieved.get(rid, []))
                )
        except Exception as e:
            observability.log_error("mapping_failed", e, batch_index=i)
            state.setdefault("warnings", []).append(
                f"Evidence mapping failed for batch starting at {i}; "
                f"marked needs_review."
            )
            for req in batch:
                rid = str(req.get("requirement_id", "?"))
                items.append(normalize_mapping({}, rid, retrieved.get(rid, [])))

    # Preserve original requirement order.
    order = [str(r.get("requirement_id", "?")) for r in requirements]
    items.sort(key=lambda d: order.index(d["requirement_id"])
               if d["requirement_id"] in order else len(order))
    state["items"] = items
    state["unmapped"] = unmapped
    observability.log_event("node_success", node="map_evidence",
                            tender_id=tender_id, mapped=len(items))
    return state


def assemble_evidence_node(state: EvidenceAgentState) -> EvidenceAgentState:
    state["final_result"] = {
        "tender_id": state.get("tender_id", "unknown"),
        "company_profile_id": state.get("company_profile_id", "sample"),
        "items": state.get("items", []),
        "unmapped_requirements": state.get("unmapped", []),
        "warnings": state.get("warnings", []),
    }
    return state
