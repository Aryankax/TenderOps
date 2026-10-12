"""P3 Compliance + Orchestrator service.

Runs the deterministic compliance graph over P1+P2 outputs, persists a
checkpoint snapshot on every run (pause AND proceed — resume needs both),
and resumes paused runs by re-mapping only affected requirements.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from app.agents.compliance_agent.graph import compliance_agent_graph
from app.agents.compliance_agent.state import ComplianceAgentState
from app.agents.evidence_agent.service import EvidenceAgentService
from app.core.config import settings
from app.models.compliance import ComplianceMatrix
from app.models.evidence import EvidenceMap
from app.models.tender import TenderAnalysisResult
from app.services.observability import observability


class ComplianceService:
    @staticmethod
    def evaluate(
        analysis: TenderAnalysisResult, evidence: EvidenceMap
    ) -> ComplianceMatrix:
        observability.log_event(
            "compliance_started", tender_id=analysis.tender_id
        )
        initial_state: ComplianceAgentState = {
            "tender_id": analysis.tender_id,
            "analysis": analysis.model_dump(),
            "evidence_map": evidence.model_dump(),
            "matrix": None,
            "decision": "UNKNOWN",
            "errors": [],
        }
        try:
            import neatlogs

            handler = neatlogs.langchain_handler()
            final_state = compliance_agent_graph.invoke(
                initial_state, config={"callbacks": [handler]}
            )
            matrix = final_state.get("matrix")
            if not matrix:
                raise RuntimeError(
                    "; ".join(final_state.get("errors", ["empty matrix"]))
                )
            observability.log_event(
                "compliance_completed",
                tender_id=analysis.tender_id,
                decision=final_state.get("decision", "UNKNOWN"),
            )
            return ComplianceMatrix(**matrix)
        except Exception as e:
            observability.log_error(
                "compliance_failed", e, tender_id=analysis.tender_id
            )
            raise RuntimeError(f"Compliance evaluation failed: {e!s}")

    # -- checkpoints ------------------------------------------------------
    @staticmethod
    def checkpoint_path(tender_id: str) -> str:
        os.makedirs(settings.CHECKPOINT_DIR, exist_ok=True)
        safe = "".join(c for c in tender_id if c.isalnum() or c in "-_")[:64]
        return os.path.join(settings.CHECKPOINT_DIR, f"{safe or 'run'}.json")

    @staticmethod
    def save_checkpoint(payload: dict[str, Any]) -> str:
        tender_id = str(payload.get("tender_id", "unknown"))
        path = ComplianceService.checkpoint_path(tender_id)
        snapshot = {
            **payload,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False)
        observability.log_event(
            "checkpoint_saved", tender_id=tender_id, path=path
        )
        return path

    @staticmethod
    def load_checkpoint(tender_id: str) -> dict[str, Any]:
        path = ComplianceService.checkpoint_path(tender_id)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"No checkpoint for tender {tender_id}.")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    # -- resume -----------------------------------------------------------
    @staticmethod
    def resume_pipeline(
        tender_id: str,
        new_docs: list[dict[str, str]] | None = None,
        company_profile_id: str = "sample",
        waived_blocker_ids: list[str] | None = None,
        fix_map: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        """Reload a checkpoint and re-run affected checks (P3 plan).

        New company docs (if any) are APPENDED to the index, then only
        affected requirements (previously missing/needs_review/partial or
        blocker-linked) are re-mapped before a fresh compliance pass.
        fix_map ({blocker_id: [fix filenames]}) scopes the re-check per
        blocker: each blocker's requirements are re-analyzed ONLY against
        its own attached fix files, and the outcome is logged per blocker
        so the UI can show exactly which file resolved which blocker.
        waived_blocker_ids records explicit human overrides: those blockers
        move to waived_blockers (auditable) and no longer pause the run.
        When the run unblocks, P4 (bid drafting + verification) executes so
        the pipeline actually finishes.
        Returns {"analysis", "evidence", "compliance", "package", "verification"}.
        """
        snapshot = ComplianceService.load_checkpoint(tender_id)
        analysis = TenderAnalysisResult(**snapshot["analysis"])
        evidence = EvidenceMap(**snapshot["evidence"])

        if new_docs:
            from app.agents.evidence_agent.retrieval import index_company_docs

            stats = index_company_docs(new_docs, company_profile_id, replace=False)
            observability.log_event(
                "resume_index_appended", tender_id=tender_id, **stats
            )

        fix_map = {k: [f for f in (v or []) if f] for k, v in (fix_map or {}).items()}
        fix_map = {k: v for k, v in fix_map.items() if v}
        if fix_map:
            evidence = ComplianceService._resume_scoped_fix_map(
                tender_id, analysis, evidence, company_profile_id, snapshot, fix_map
            )
        elif new_docs or not waived_blocker_ids:
            affected = ComplianceService._affected_requirement_ids(
                evidence, snapshot.get("compliance", {})
            )
            remapped = EvidenceAgentService.map_evidence(
                analysis, company_profile_id, requirement_ids=affected or None
            )
            evidence = ComplianceService._merge_evidence(evidence, remapped)
        # Else: waive-only resume — evidence is byte-identical to the snapshot,
        # so re-mapping would just burn LLM budget to reproduce it. Straight
        # to re-evaluation + waivers (fast, and blocker IDs stay stable).

        compliance = ComplianceService.evaluate(analysis, evidence)
        # Stable waivers: BLK-xxx IDs are renumbered on every evaluation, so
        # an ID waived last round may point at a different finding (or nothing)
        # after a fix resolves something. Match by finding fingerprint
        # (type + requirements + description) resolved against the OLD
        # snapshot, and carry previously-waived findings forward while their
        # evidence is unchanged.
        old_compliance = snapshot.get("compliance", {}) or {}
        old_by_id = {
            str(b.get("blocker_id", "")): ComplianceService._blocker_fingerprint(b)
            for b in old_compliance.get("blockers", []) or []
        }
        carried_fps = {
            ComplianceService._blocker_fingerprint(b)
            for b in old_compliance.get("waived_blockers", []) or []
        }
        requested_fps = {
            old_by_id[i] for i in (waived_blocker_ids or []) if i in old_by_id
        }
        waive_fps = carried_fps | requested_fps
        kept, waived = [], []
        for blocker in compliance.blockers:
            (waived if ComplianceService._blocker_fingerprint(blocker) in waive_fps
             else kept).append(blocker)
        if [b for b in (waived_blocker_ids or []) if b not in old_by_id]:
            observability.log_event(
                "blockers_waive_unmatched", tender_id=tender_id,
                blocker_ids=[b for b in (waived_blocker_ids or []) if b not in old_by_id],
            )
        if waived:
            observability.log_event(
                "blockers_waived", tender_id=tender_id,
                blocker_ids=[b.blocker_id for b in waived],
            )
        compliance = compliance.model_copy(
            update={
                "blockers": kept,
                "waived_blockers": [
                    *compliance.waived_blockers, *waived
                ],
                "summary": {
                    **compliance.summary, "blockers": len(kept),
                    "waived": len(compliance.waived_blockers) + len(waived),
                },
                "decision": "PAUSE" if kept else "PROCEED",
            }
        )
        package = None
        verification = None
        if compliance.decision == "PROCEED" and not kept:
            # Unblocked: finish the pipeline (P4 draft + verify) so resume
            # yields a bid package, not just a compliance matrix.
            package, verification = ComplianceService._run_p4_on_unblock(
                tender_id, analysis, evidence, compliance
            )
        ComplianceService.save_checkpoint(
            {
                "tender_id": analysis.tender_id,
                "document_name": snapshot.get("document_name", ""),
                "analysis": analysis.model_dump(),
                "evidence": evidence.model_dump(),
                "compliance": compliance.model_dump(),
                "package": package.model_dump() if package is not None else None,
                "verification": verification.model_dump() if verification is not None else None,
            }
        )
        return {
            "analysis": analysis,
            "evidence": evidence,
            "compliance": compliance,
            "package": package,
            "verification": verification,
        }

    @staticmethod
    def _blocker_fingerprint(blocker: Any) -> tuple[str, tuple[str, ...], str]:
        """Stable identity for a finding across re-evaluations.

        BLK-xxx IDs are encounter-order numbers regenerated on every run, so
        they go stale the moment a fix resolves anything above them. The
        (type, requirements, description) triple is deterministic output of the
        rules engine — identical while the underlying finding is unchanged,
        different when new evidence changes the picture (which should
        re-surface for human review, not stay silently waived).
        """
        if isinstance(blocker, dict):
            btype = str(blocker.get("blocker_type", ""))
            reqs = blocker.get("affected_requirement_ids", []) or []
            desc = str(blocker.get("description", ""))
        else:
            btype = getattr(blocker.blocker_type, "value", blocker.blocker_type)
            reqs = blocker.affected_requirement_ids or []
            desc = blocker.description or ""
        return (str(btype), tuple(sorted(str(r) for r in reqs)), desc[:500])

    @staticmethod
    def _run_p4_on_unblock(
        tender_id: str,
        analysis: TenderAnalysisResult,
        evidence: EvidenceMap,
        compliance: ComplianceMatrix,
    ) -> tuple[Any | None, Any | None]:
        """Draft + verify once blockers clear (P4), mirroring orchestrator nodes."""
        from app.agents.bidprep_agent.service import BidPrepService
        from app.agents.verification_agent.service import VerificationService

        try:
            observability.log_event(
                "resume_p4_started", tender_id=tender_id,
                draftable=sum(
                    1 for i in compliance.items
                    if str(getattr(i.status, "value", i.status)) == "COMPLIANT"
                ),
            )
            package = BidPrepService.build_package(analysis, evidence, compliance)
            report = VerificationService.verify(
                tender_id=analysis.tender_id,
                requirements=[r.model_dump() for r in analysis.requirements],
                evidence_items=[i.model_dump() for i in evidence.items],
                drafts=[d.model_dump() for d in package.drafts],
                risk_report=list(package.risk_report),
                blockers=[b.model_dump() for b in compliance.blockers],
                compliance_items=[i.model_dump() for i in compliance.items],
            )
            observability.log_event(
                "resume_p4_completed", tender_id=tender_id, passed=report.passed
            )
            return package, report
        except Exception as e:  # noqa: BLE001 — P4 failure must not re-block resolved work
            observability.log_error("resume_p4_failed", e, tender_id=tender_id)
            return None, None

    @staticmethod
    def _resume_scoped_fix_map(
        tender_id: str,
        analysis: TenderAnalysisResult,
        evidence: EvidenceMap,
        company_profile_id: str,
        snapshot: dict[str, Any],
        fix_map: dict[str, list[str]],
    ) -> EvidenceMap:
        """Per-blocker re-check: each blocker's requirements re-analyzed ONLY
        against its own attached fix files.

        Returns the merged evidence map. Every step is logged
        (resume_blocker_recheck with per-requirement outcomes) so the UI
        tracker shows exactly which file resolved which blocker.
        """
        known_ids = {str(r.requirement_id) for r in analysis.requirements}
        blockers = {
            str(b.get("blocker_id", "")): b
            for b in (snapshot.get("compliance", {}) or {}).get("blockers", [])
        }
        merged = evidence
        for blocker_id, files in fix_map.items():
            blocker = blockers.get(blocker_id)
            if blocker is None:
                observability.log_event(
                    "resume_blocker_skipped", tender_id=tender_id,
                    blocker_id=blocker_id, reason="unknown blocker — already resolved or waived",
                )
                continue
            req_ids = sorted(
                {
                    str(r)
                    for r in (blocker.get("affected_requirement_ids", []) or [])
                    if str(r) in known_ids
                }
            )
            if not req_ids:
                # Nothing re-checkable (e.g. tender-level contradiction with no
                # requirement scope): NEVER fall through to an unscoped remap —
                # that would replace good evidence with fix-file-only misses.
                observability.log_event(
                    "resume_blocker_skipped", tender_id=tender_id,
                    blocker_id=blocker_id, reason="no affected requirements to re-check",
                )
                continue
            observability.log_event(
                "resume_blocker_recheck", tender_id=tender_id,
                blocker_id=blocker_id, files=files, requirements=req_ids,
                phase="started",
            )
            remapped = EvidenceAgentService.map_evidence(
                analysis, company_profile_id,
                requirement_ids=req_ids, source_documents=files,
            )
            merged = ComplianceService._merge_evidence(merged, remapped)
            by_id = {i.requirement_id: i.status for i in remapped.items}
            observability.log_event(
                "resume_blocker_recheck", tender_id=tender_id,
                blocker_id=blocker_id, files=files, requirements=req_ids,
                phase="completed",
                outcomes={r: by_id.get(r, "unchanged") for r in req_ids},
            )
        return merged

    @staticmethod
    def _affected_requirement_ids(
        evidence: EvidenceMap, compliance: dict[str, Any]
    ) -> list[str]:
        ids = {
            i.requirement_id
            for i in evidence.items
            if i.status in ("missing", "needs_review", "partial")
        }
        for b in (compliance or {}).get("blockers", []) or []:
            ids.update(str(r) for r in (b.get("affected_requirement_ids", []) or []))
        return sorted(ids)

    @staticmethod
    def _merge_evidence(old: EvidenceMap, new: EvidenceMap) -> EvidenceMap:
        """Replace re-mapped items, recompute the unmapped list."""
        fresh = {i.requirement_id: i for i in new.items}
        merged = [fresh.get(i.requirement_id, i) for i in old.items]
        for item in new.items:
            if item.requirement_id not in {i.requirement_id for i in old.items}:
                merged.append(item)
        unmapped = [
            i.requirement_id
            for i in merged
            if i.status in ("missing", "needs_review")
        ]
        return EvidenceMap(
            tender_id=new.tender_id,
            company_profile_id=new.company_profile_id,
            items=merged,
            unmapped_requirements=sorted(set(unmapped)),
            warnings=[*old.warnings, *new.warnings],
        )
