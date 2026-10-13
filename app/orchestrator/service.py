"""Supervisor orchestrator service: one graph, every agent, crash-safe.

Runs P1 -> P2 -> P3, then routes on the compliance decision (pause on
blockers, else P4 draft -> verify with a bounded fix loop). State is
checkpointed to SQLite per run; file snapshots (pause + finalize) keep the
existing /resume contract working unchanged.
"""

from __future__ import annotations

import uuid
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from app.agents.compliance_agent.service import ComplianceService
from app.core.config import settings
from app.orchestrator.graph import build_orchestrator_graph
from app.orchestrator.state import OrchestratorState
from app.services.observability import observability


def _graph_db_path() -> str:
    import os

    os.makedirs(settings.CHECKPOINT_DIR, exist_ok=True)
    return f"{settings.CHECKPOINT_DIR.rstrip('/')}/graph_checkpoints.sqlite"


class OrchestratorService:
    @staticmethod
    def run_tender(
        pdf_path: str,
        document_name: str,
        company_profile_id: str = "sample",
        fetch_links: bool = True,
    ) -> dict[str, Any]:
        """Full supervised run. Returns model dicts + status + checkpoint."""
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        observability.log_event(
            "orchestrator_started", run_id=run_id, document_name=document_name
        )
        initial: OrchestratorState = {
            "run_id": run_id,
            "tender_id": "pending",
            "document_name": document_name,
            "company_profile_id": company_profile_id,
            "tender_pdf_path": pdf_path,
            "fetch_links": fetch_links,
            "analysis": None,
            "evidence": None,
            "compliance": None,
            "package": None,
            "verification": None,
            "fix_attempts": 0,
            "pipeline_status": "RUNNING",
            "checkpoint_path": None,
            "errors": [],
        }
        try:
            import neatlogs

            with SqliteSaver.from_conn_string(_graph_db_path()) as checkpointer:
                graph = build_orchestrator_graph(checkpointer)
                final_state = graph.invoke(
                    initial,
                    config={
                        "callbacks": [neatlogs.langchain_handler()],
                        "configurable": {"thread_id": run_id},
                    },
                )
        except Exception as e:
            observability.log_error("orchestrator_failed", e, run_id=run_id)
            raise RuntimeError(f"Pipeline orchestration failed: {e!s}")
        # File snapshot keeps the /resume contract (independent of sqlite).
        snapshot = {
            "tender_id": final_state.get("tender_id", "pending"),
            "document_name": document_name,
            "analysis": final_state.get("analysis"),
            "evidence": final_state.get("evidence"),
            "compliance": final_state.get("compliance"),
            "package": final_state.get("package"),
            "verification": final_state.get("verification"),
            "pipeline_status": final_state.get("pipeline_status", "RUNNING"),
        }
        checkpoint_path = ComplianceService.save_checkpoint(snapshot)
        observability.log_event(
            "orchestrator_completed", run_id=run_id,
            status=final_state.get("pipeline_status"),
        )
        _record_entire_checkpoint(run_id, document_name, final_state,
                                  checkpoint_path)
        return {**snapshot, "checkpoint_path": checkpoint_path, "run_id": run_id}


def _record_entire_checkpoint(run_id: str, document_name: str,
                              final_state: dict[str, Any], checkpoint_path: str | None) -> None:
    """Best-effort Entire checkpoint so future agents learn from this run.

    Never fails the pipeline: Entire is memory, not the critical path.
    """
    try:
        if not settings.ENTIRE_ENABLED:
            return
        from app.services import entire_memory as mem

        compliance = final_state.get("compliance") or {}
        verification = final_state.get("verification") or {}
        analysis = final_state.get("analysis") or {}
        reqs = analysis.get("requirements", []) if isinstance(analysis, dict) else []
        decisions = [
            f"P1 extracted {len(reqs)} requirements",
            (
                f"P3 decision={compliance.get('decision', '?')} "
                f"blockers={len(compliance.get('blockers', []) or [])}"
            ),
        ]
        if verification:
            decisions.append(
                f"P4 verified passed={verification.get('passed')} "
                f"findings={len(verification.get('findings', []) or [])}"
            )
        mem.record_checkpoint(
            tender_id=str(final_state.get("tender_id", "pending")),
            request=f"Supervised tender run for {document_name}",
            decisions=decisions,
            changed_files=["app/orchestrator/graph.py", "app/orchestrator/nodes.py"],
            validation={
                "pipeline_status": final_state.get("pipeline_status"),
                "checkpoint_path": checkpoint_path,
            },
            run_id=run_id,
        )
        observability.log_event("entire_checkpoint_recorded", run_id=run_id)
    except Exception as e:  # noqa: BLE001 — memory must not break runs
        observability.log_error("entire_checkpoint_failed", e, run_id=run_id)
