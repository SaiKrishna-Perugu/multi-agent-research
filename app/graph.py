"""
The multi-agent orchestration graph:

    researcher -> analyst -> writer -> human_review -> [approved?] -> finalize -> END
                                            ^                 |
                                            |   (revise,      |
                                            +-- retries left) -+
                                                                (revise, retries
                                                                 exhausted)
                                                                     |
                                                                     v
                                                                 finalize (forced,
                                                                 with a note)

`human_review` uses LangGraph's interrupt() -- execution genuinely pauses
here (persisted via a checkpointer) until an external call resumes it with
the reviewer's decision. This is real human-in-the-loop, not a placeholder:
the graph is not "waiting" in a running process; it's suspended, and a
completely separate HTTP request (potentially hours later) can resume it.

MAX_REVISIONS bounds the review loop the same way rag-capstone's MAX_RETRIES
bounds its rewrite loop -- a human could otherwise request revisions
indefinitely; after the cap, the graph force-finalizes with the current
best draft rather than looping forever.
"""
from typing import Literal, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt
from langsmith import traceable

from app.agents import analyst_node, researcher_node, writer_node

MAX_REVISIONS = 3


class ResearchState(TypedDict):
    topic: str
    sub_queries: list
    research_notes: str
    sources: list
    analysis: str
    draft: str
    revision_feedback: str
    revision_count: int
    final_report: str
    status: str


@traceable(name="agent.human_review", run_type="chain")
def human_review_node(state: ResearchState) -> ResearchState:
    decision = interrupt({
        "draft": state["draft"],
        "revision_count": state["revision_count"],
        "message": "Review the draft. Resume with {'approved': True} to "
                    "finalize, or {'approved': False, 'feedback': '...'} to request changes.",
    })
    approved = bool(decision.get("approved", False))
    feedback = decision.get("feedback", "") if not approved else ""
    return {
        "revision_feedback": feedback,
        "revision_count": state["revision_count"] + (0 if approved else 1),
        "status": "approved" if approved else "revision_requested",
    }


def route_after_review(state: ResearchState) -> Literal["finalize", "writer"]:
    if state["status"] == "approved":
        return "finalize"
    if state["revision_count"] > MAX_REVISIONS:
        return "finalize"  # cap reached -- force finalize with current draft
    return "writer"


@traceable(name="agent.finalize", run_type="chain")
def finalize_node(state: ResearchState) -> ResearchState:
    note = ""
    if state["status"] == "revision_requested" and state["revision_count"] > MAX_REVISIONS:
        note = (f"\n\n---\n*Note: maximum revision limit ({MAX_REVISIONS}) reached. "
                f"This is the most recent draft; further review is recommended.*")
    return {"final_report": state["draft"] + note, "status": "finalized"}


def build_graph():
    graph = StateGraph(ResearchState)

    graph.add_node("researcher", researcher_node)
    graph.add_node("analyst", analyst_node)
    graph.add_node("writer", writer_node)
    graph.add_node("human_review", human_review_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("researcher")
    graph.add_edge("researcher", "analyst")
    graph.add_edge("analyst", "writer")
    graph.add_edge("writer", "human_review")
    graph.add_conditional_edges(
        "human_review",
        route_after_review,
        {"finalize": "finalize", "writer": "writer"},
    )
    graph.add_edge("finalize", END)

    return graph


def compile_graph(checkpointer):
    """Compile with the given checkpointer -- injected by main.py so the
    same graph definition can run against MemorySaver (dev) or a persisted
    backend (production) without changing this module."""
    return build_graph().compile(checkpointer=checkpointer)
