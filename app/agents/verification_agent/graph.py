from langgraph.graph import END, StateGraph

from app.agents.verification_agent.nodes import verify_node
from app.agents.verification_agent.state import VerificationAgentState


def build_graph():
    builder = StateGraph(VerificationAgentState)
    builder.add_node("verify", verify_node)
    builder.set_entry_point("verify")
    builder.add_edge("verify", END)
    return builder.compile()


verification_agent_graph = build_graph()
