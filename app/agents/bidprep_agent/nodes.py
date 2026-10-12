from typing import Any

from app.agents.bidprep_agent.llm_schemas import LLMDraftList, normalize_draft
from app.agents.bidprep_agent.prompts import DRAFTING_PROMPT, FIX_SECTION_TEMPLATE
from app.agents.bidprep_agent.state import BidPrepAgentState
from app.agents.tender_analyst.nodes import _invoke_structured
from app.core.config import settings
from app.services.observability import observability


def _format_batch(
    batch: list[dict[str, Any]],
    evidence_by_id: dict[str, list[dict[str, Any]]],
    compliance_by_id: dict[str, str],
) -> str:
    blocks = []
    for req in batch:
        rid = str(req.get("requirement_id", "?"))
        lines = [
            f"Requirement {rid} [{compliance_by_id.get(rid, '?')}]: {req.get('title', '')}",
            f"  Details: {(req.get('normalized_description') or '')[:300]}",
        ]
        th = req.get("threshold") or {}
        if th.get("value") is not None:
            lines.append(
                f"  Figure: {th.get('operator', '=')} {th.get('value')} {th.get('unit', '')}"
            )
        for h in evidence_by_id.get(rid, [])[:6]:
            lines.append(
                f"  Excerpt [{h.get('document_name', '?')} p.{h.get('page_number', '?')}]: "
                f"{(h.get('excerpt', '') or '')[:500]}"
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _default_kind(req: dict[str, Any]) -> str:
    if str(req.get("category", "")).upper() == "MANDATORY_DOCUMENTS":
        return "declaration"
    if str(req.get("semantic_type", "")).upper() == "INFORMATIONAL":
        return "annexure_stub"
    return "requirement_response"


def draft_node(state: BidPrepAgentState) -> BidPrepAgentState:
    tender_id = state.get("tender_id", "unknown")
    observability.log_event("node_start", node="draft_responses", tender_id=tender_id)
    requirements = state.get("requirements", [])
    evidence_by_id = state.get("evidence_by_id", {})
    compliance_by_id = state.get("compliance_by_id", {})
    fixes = state.get("findings_to_fix", [])
    fix_section = ""
    if fixes:
        fix_lines = [
            f"- {f.get('draft_id', '?')} ({f.get('check', '?')}): {f.get('detail', '')}"
            for f in fixes[:20]
        ]
        fix_section = FIX_SECTION_TEMPLATE.format(findings="\n".join(fix_lines))

    drafts: list[dict[str, Any]] = []
    batch_size = max(1, settings.BIDPREP_BATCH_SIZE)
    for i in range(0, len(requirements), batch_size):
        batch = requirements[i : i + batch_size]
        prompt = DRAFTING_PROMPT.format(
            batch_content=_format_batch(batch, evidence_by_id, compliance_by_id),
            fix_section=fix_section,
        )
        try:
            res = _invoke_structured(
                LLMDraftList, prompt, operation=f"draft_batch_{i}"
            )
            raw_list = getattr(res, "drafts", []) or []
            by_id: dict[str, Any] = {}
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
                try:
                    drafts.append(
                        normalize_draft(
                            by_id.get(rid, {}), rid, _default_kind(req)
                        )
                    )
                except Exception:
                    continue
        except Exception as e:  # noqa: BLE001 — one bad batch skips, rest survive
            observability.log_error("draft_failed", e, batch_index=i)
            state.setdefault("warnings", []).append(
                f"Drafting failed for batch starting at {i}."
            )
    state["drafts"] = drafts
    observability.log_event(
        "node_success", node="draft_responses", tender_id=tender_id, drafts=len(drafts)
    )
    return state


def package_node(state: BidPrepAgentState) -> BidPrepAgentState:
    """Checklist + risk report from everything NOT drafted (P4 prep output)."""
    compliance_by_id = state.get("compliance_by_id", {})
    checklist: list[str] = []
    risk: list[str] = []
    for req in state.get("all_requirements", []):
        rid = str(req.get("requirement_id", "?"))
        comp = compliance_by_id.get(rid, "")
        title = str(req.get("title", rid))
        if comp == "PENDING":
            checklist.append(f"Provide evidence for '{title}' [{rid}].")
        elif comp == "NEEDS_REVIEW":
            risk.append(f"Unresolved review on '{title}' [{rid}] — decide before submission.")
        elif comp == "NON_COMPLIANT":
            risk.append(f"Failed requirement '{title}' [{rid}] — concede or supply stronger evidence.")
    state["checklist"] = checklist
    state["risk_report"] = risk
    state["final_package"] = {
        "tender_id": state.get("tender_id", "unknown"),
        "drafts": state.get("drafts", []),
        "checklist": checklist,
        "risk_report": risk,
    }
    return state
