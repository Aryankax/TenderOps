from langgraph.graph import END, StateGraph

from app.agents.bidprep_agent.nodes import draft_node, package_node
from app.agents.bidprep_agent.state import BidPrepAgentState


def build_graph():
    builder = StateGraph(BidPrepAgentState)
    builder.add_node("draft", draft_node)
    builder.add_node("package", package_node)
    builder.set_entry_point("draft")
    builder.add_edge("draft", "package")
    builder.add_edge("package", END)
    return builder.compile()


bidprep_agent_graph = build_graph()
