from langgraph.graph import END, StateGraph

from app.agents.evidence_agent.nodes import (
    assemble_evidence_node,
    map_evidence_node,
    retrieve_evidence_node,
)
from app.agents.evidence_agent.state import EvidenceAgentState


def build_graph():
    builder = StateGraph(EvidenceAgentState)
    builder.add_node("retrieve_evidence", retrieve_evidence_node)
    builder.add_node("map_evidence", map_evidence_node)
    builder.add_node("assemble", assemble_evidence_node)
    builder.set_entry_point("retrieve_evidence")
    builder.add_edge("retrieve_evidence", "map_evidence")
    builder.add_edge("map_evidence", "assemble")
    builder.add_edge("assemble", END)
    return builder.compile()


evidence_agent_graph = build_graph()
