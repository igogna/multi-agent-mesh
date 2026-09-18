"""Builds the LangGraph version of the requirement -> plan -> code -> tests ->
review loop. Reuses core/routing.py's decide_after_plan_review/
decide_after_tests/decide_after_review directly as conditional-edge
functions -- no branching logic is duplicated here or in nodes.py.

Scope for this first pass: analyze -> human plan gate -> generate ->
generate_tests -> run_tests -> review. GitHub push/PR/secret-scan/human-PR-
review stay outside the graph, same as before (see
adapters/cli_adapter/run.py::regenerate_and_push for that half).

The module-level `graph` (no checkpointer attached) is what langgraph.json
points LangGraph Studio/`langgraph dev` at -- the platform's dev server
supplies its own persistence layer, so a graph meant to be loaded by it
should not hardcode one (see docs.langchain.com/oss/python/langgraph/studio).
adapters/langgraph_adapter/run.py, which drives interrupts itself in-process,
calls build_graph(checkpointer=MemorySaver()) instead.
"""

from langgraph.graph import END, StateGraph

from adapters.langgraph_adapter.nodes import (
    analyze_node,
    generate_node,
    generate_tests_node,
    human_plan_gate_node,
    review_node,
    run_tests_node,
)
from core.models import RunState
from core.routing import decide_after_plan_review, decide_after_review, decide_after_tests


def build_graph(checkpointer=None):
    graph = StateGraph(RunState)

    graph.add_node("analyze", analyze_node)
    graph.add_node("human_plan_gate", human_plan_gate_node)
    graph.add_node("generate", generate_node)
    graph.add_node("generate_tests", generate_tests_node)
    graph.add_node("run_tests", run_tests_node)
    graph.add_node("review", review_node)

    graph.set_entry_point("analyze")
    graph.add_edge("analyze", "human_plan_gate")
    graph.add_conditional_edges(
        "human_plan_gate",
        decide_after_plan_review,
        {"proceed": "generate", "retry": "analyze", "escalate": END},
    )

    graph.add_edge("generate", "generate_tests")
    graph.add_edge("generate_tests", "run_tests")
    graph.add_conditional_edges(
        "run_tests",
        decide_after_tests,
        {"proceed": "review", "retry": "generate", "escalate": END},
    )

    graph.add_conditional_edges(
        "review",
        decide_after_review,
        {"merge_gate": END, "retry": "generate", "escalate": END},
    )

    return graph.compile(checkpointer=checkpointer)


# For LangGraph Studio / `langgraph dev` (see langgraph.json) -- no
# checkpointer, per the module docstring above.
graph = build_graph()
