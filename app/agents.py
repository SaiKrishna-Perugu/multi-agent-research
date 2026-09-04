"""
The three specialized agents. Each is a plain function (question in,
structured output out) -- graph.py wires them into a LangGraph state
machine with a human-in-the-loop checkpoint between "writer" and "finalize".

Each agent can be pinned to its own model via config's per-agent overrides
(see app/providers.py) -- e.g. a cheaper/faster model for the researcher's
many summarization calls, a stronger one for the writer's final prose.
"""

import json

from langsmith import traceable
from pydantic import BaseModel, Field

from app import config
from app.providers import get_llm
from app.tools import run_multi_search


class DecomposedQueries(BaseModel):
    queries: list[str] = Field(
        default_factory=list,
        description="List of 2 to 4 distinct, focused search queries",
    )


_DECOMPOSE_SYSTEM_PROMPT = """You are a research planner. Given a topic, \
break it into up to {n} focused, distinct search-engine queries that \
together would give comprehensive coverage of the topic -- different \
angles, not rephrasings of the same question.

Respond with ONLY a JSON array of strings, e.g. ["query one", "query two"]."""

_SYNTHESIZE_SYSTEM_PROMPT = """You are a research analyst synthesizing raw \
web search results into clear notes. For each distinct fact or claim, cite \
its source using the URL in brackets, e.g. "Revenue grew 12% in 2025 \
[https://example.com/article]." Group related findings together. Note \
explicitly where sources disagree or where information is missing or \
unclear -- do not paper over gaps or contradictions."""

_ANALYSIS_SYSTEM_PROMPT = """You are a senior analyst. Given research notes \
on a topic, produce a structured analysis with these sections:

## Key Themes
(the 2-4 main threads running through the research)

## Notable Insights
(specific, non-obvious findings worth highlighting)

## Gaps & Contradictions
(what the research notes flagged as missing, unclear, or conflicting \
between sources -- be explicit here, do not smooth this over)

Base this ONLY on the provided research notes -- do not introduce outside \
claims or fill gaps with assumptions."""

_WRITER_SYSTEM_PROMPT = """You are a report writer. Given a topic and a \
structured analysis, write a clear, well-organized report in Markdown for \
a general professional audience. Structure: a brief executive summary, \
2-4 body sections following the analysis's key themes, and a short \
"Open Questions" section drawn from the analysis's gaps/contradictions. \
Cite sources inline using the URLs present in the analysis/notes where \
specific claims are made, formatted as standard Markdown links, e.g. \
"Revenue grew 12% in 2025 ([source](https://example.com/article))" -- \
never bare URLs and never any other bracket style. Keep it factual and \
grounded in the provided material -- do not invent statistics, quotes, or \
sources."""

_REVISION_SYSTEM_PROMPT = """You are revising a report draft based on \
reviewer feedback. You have access to the topic, the structured analysis, \
the research notes, the current draft, and the reviewer feedback. \
Directly address the feedback given while preserving all accurate findings \
that were not flagged. Return the complete revised report, not just \
the changed portion."""


def _clean_json_markdown(text: str) -> str:
    """Safely extract JSON from markdown code fences if present."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


@traceable(name="agent.researcher", run_type="chain")
def researcher_node(state: dict) -> dict:
    llm = get_llm(temperature=0.2, model_override=config.RESEARCHER_MODEL_OVERRIDE)

    decompose_prompt = _DECOMPOSE_SYSTEM_PROMPT.format(n=config.MAX_RESEARCH_QUERIES)
    sub_queries = None

    topic_context = state["topic"]
    if state.get("review_action") == "research_gap" and state.get("revision_feedback"):
        topic_context = (
            f"Topic: {state['topic']}\n"
            f"Target Follow-up Research Focus: {state['revision_feedback']}"
        )

    if hasattr(llm, "with_structured_output"):
        try:
            structured_llm = llm.with_structured_output(DecomposedQueries)
            structured_res = structured_llm.invoke(
                [
                    ("system", decompose_prompt),
                    ("human", topic_context),
                ]
            )
            if isinstance(structured_res, DecomposedQueries) and structured_res.queries:
                sub_queries = structured_res.queries
            elif isinstance(structured_res, dict) and structured_res.get("queries"):
                sub_queries = structured_res["queries"]
        except Exception:
            sub_queries = None

    if not sub_queries:
        response = llm.invoke(
            [
                ("system", decompose_prompt),
                ("human", topic_context),
            ]
        )
        try:
            raw_json = _clean_json_markdown(str(response.content))
            sub_queries = json.loads(raw_json)
            if not isinstance(sub_queries, list) or not sub_queries:
                raise ValueError("empty or non-list response")
        except (json.JSONDecodeError, ValueError):
            # Fall back to a single query using the raw topic -- degraded but
            # not a failure. A malformed decomposition shouldn't abort the whole
            # research pass when searching the raw topic still produces useful results.
            sub_queries = [state["topic"]]

    search_results = run_multi_search(sub_queries)

    results_text = "\n\n".join(
        f"Query: {r.query}\n"
        + "\n".join(
            f"- {item['title']} [{item['url']}]: {item['content'][:1500]}"
            for item in r.results
        )
        for r in search_results
    )

    synthesis = llm.invoke(
        [
            ("system", _SYNTHESIZE_SYSTEM_PROMPT),
            (
                "human",
                f"Topic: {state['topic']}\n\nRAW SEARCH RESULTS:\n{results_text}",
            ),
        ]
    )

    new_notes = str(synthesis.content)
    existing_notes = state.get("research_notes", "")
    if existing_notes and state.get("review_action") == "research_gap":
        combined_notes = (
            f"{existing_notes}\n\n### Follow-up Research Notes:\n{new_notes}"
        )
    else:
        combined_notes = new_notes

    existing_sources = state.get("sources", [])
    seen_urls = {
        s.get("url") for s in existing_sources if isinstance(s, dict) and s.get("url")
    }
    all_sources = (
        list(existing_sources) if state.get("review_action") == "research_gap" else []
    )
    for r in search_results:
        for item in r.results:
            url = item.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_sources.append({"title": item.get("title", ""), "url": url})

    return {
        "sub_queries": sub_queries,
        "research_notes": combined_notes,
        "sources": all_sources,
        "status": "researched",
        "review_action": "",
    }


@traceable(name="agent.analyst", run_type="chain")
def analyst_node(state: dict) -> dict:
    llm = get_llm(temperature=0.1, model_override=config.ANALYST_MODEL_OVERRIDE)
    response = llm.invoke(
        [
            ("system", _ANALYSIS_SYSTEM_PROMPT),
            (
                "human",
                f"Topic: {state['topic']}\n\nRESEARCH NOTES:\n{state['research_notes']}",
            ),
        ]
    )
    return {"analysis": str(response.content), "status": "analyzed"}


@traceable(name="agent.writer", run_type="chain")
def writer_node(state: dict) -> dict:
    llm = get_llm(temperature=0.4, model_override=config.WRITER_MODEL_OVERRIDE)

    if state.get("revision_feedback"):
        # Revision pass: rewrite the existing draft based on feedback,
        # grounded in the original analysis and research notes.
        human_content = (
            f"Topic: {state['topic']}\n\n"
            f"ANALYSIS:\n{state.get('analysis', '')}\n\n"
            f"RESEARCH NOTES:\n{state.get('research_notes', '')}\n\n"
            f"CURRENT DRAFT:\n{state['draft']}\n\n"
            f"REVIEWER FEEDBACK:\n{state['revision_feedback']}"
        )
        response = llm.invoke(
            [
                ("system", _REVISION_SYSTEM_PROMPT),
                ("human", human_content),
            ]
        )
    else:
        # First pass: write from scratch based on the analysis.
        response = llm.invoke(
            [
                ("system", _WRITER_SYSTEM_PROMPT),
                ("human", f"Topic: {state['topic']}\n\nANALYSIS:\n{state['analysis']}"),
            ]
        )

    return {
        "draft": str(response.content),
        "revision_feedback": "",  # consumed -- clear it so the next pass starts fresh
        "status": "drafted",
    }
