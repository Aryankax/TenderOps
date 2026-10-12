from langgraph.graph import END, StateGraph

from app.orchestrator.nodes import (
    finalize_node,
    p1_node,
    p2_node,
    p3_node,
    p4_draft_node,
    p4_verify_node,
    pause_node,
)
from app.orchestrator.state import OrchestratorState


def route_after_p3(state: OrchestratorState) -> str:
    # Blockers (or an unknown decision) pause safe: never draft blind.
    decision = (state.get("compliance") or {}).get("decision", "UNKNOWN")
    return "p4_draft" if decision == "PROCEED" else "pause"


def route_after_verify(state: OrchestratorState) -> str:
    report = state.get("verification") or {}
    if report.get("passed", False):
        return "finalize"
    if int(state.get("fix_attempts", 0)) >= 2:
        # Bounded loop: ship with risks flagged rather than spin forever.
        return "finalize"
    return "fix_retry"


def fix_retry_node(state: OrchestratorState) -> OrchestratorState:
    state["fix_attempts"] = int(state.get("fix_attempts", 0)) + 1
    return state


def build_orchestrator_graph(checkpointer=None):
    builder = StateGraph(OrchestratorState)
    builder.add_node("p1", p1_node)
    builder.add_node("p2", p2_node)
    builder.add_node("p3", p3_node)
    builder.add_node("pause", pause_node)
    builder.add_node("p4_draft", p4_draft_node)
    builder.add_node("p4_verify", p4_verify_node)
    builder.add_node("fix_retry", fix_retry_node)
    builder.add_node("finalize", finalize_node)
    builder.set_entry_point("p1")
    builder.add_edge("p1", "p2")
    builder.add_edge("p2", "p3")
    builder.add_conditional_edges("p3", route_after_p3, {"p4_draft": "p4_draft", "pause": "pause"})
    builder.add_edge("p4_draft", "p4_verify")
    builder.add_conditional_edges(
        "p4_verify", route_after_verify,
        {"finalize": "finalize", "fix_retry": "fix_retry"},
    )
    builder.add_edge("fix_retry", "p4_draft")
    builder.add_edge("pause", END)
    builder.add_edge("finalize", END)
    if checkpointer is not None:
        return builder.compile(checkpointer=checkpointer)
    return builder.compile()


orchestrator_graph = build_orchestrator_graph()
