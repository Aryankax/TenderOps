from langgraph.graph import StateGraph, END
from app.agents.tender_analyst.state import TenderAnalystState
from app.agents.tender_analyst.nodes import (
    extract_metadata_node,
    extract_requirements_by_chunk_node,
    detect_issues_node,
    assemble_final_result_node
)

from app.core.config import settings

def route_validation(state: TenderAnalystState):
    # We will just assemble the final result even if there are validation errors,
    # as retrying with temperature 0.0 often repeats the same error.
    # Partial results are preserved; issue detection still runs over them.
    return "detect_issues"

def build_graph():
    builder = StateGraph(TenderAnalystState)
    
    builder.add_node("extract_metadata", extract_metadata_node)
    builder.add_node("extract_requirements", extract_requirements_by_chunk_node)
    builder.add_node("detect_issues", detect_issues_node)
    builder.add_node("assemble_final", assemble_final_result_node)
    
    builder.set_entry_point("extract_metadata")
    builder.add_edge("extract_metadata", "extract_requirements")
    builder.add_conditional_edges("extract_requirements", route_validation)
    builder.add_edge("detect_issues", "assemble_final")
    builder.add_edge("assemble_final", END)
    
    return builder.compile()

tender_analyst_graph = build_graph()
