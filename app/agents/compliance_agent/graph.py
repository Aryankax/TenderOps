from langgraph.graph import END, StateGraph

from app.agents.compliance_agent.nodes import (
    assess_blockers_node,
    compliance_check_node,
    finalize_result_node,
    pause_checkpoint_node,
)
from app.agents.compliance_agent.state import ComplianceAgentState


def route_blockers(state: ComplianceAgentState) -> str:
    # UNKNOWN (evaluation crashed) pauses safe: never proceed blind.
    return "pause_checkpoint" if state.get("decision", "UNKNOWN") != "PROCEED" else "finalize"


def build_graph():
    builder = StateGraph(ComplianceAgentState)
    builder.add_node("compliance_check", compliance_check_node)
    builder.add_node("assess_blockers", assess_blockers_node)
    builder.add_node("pause_checkpoint", pause_checkpoint_node)
    builder.add_node("finalize", finalize_result_node)
    builder.set_entry_point("compliance_check")
    builder.add_edge("compliance_check", "assess_blockers")
    builder.add_conditional_edges("assess_blockers", route_blockers)
    builder.add_edge("pause_checkpoint", END)
    builder.add_edge("finalize", END)
    return builder.compile()


compliance_agent_graph = build_graph()
