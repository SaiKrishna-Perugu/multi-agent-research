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

from app import config
from app.providers import get_llm
from app.tools import run_multi_search

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
reviewer feedback. Keep everything that wasn't flagged, and directly \
address the feedback given. Return the complete revised report, not just \
the changed portion."""


@traceable(name="agent.researcher", run_type="chain")
def researcher_node(state: dict) -> dict:
    llm = get_llm(temperature=0.2, model_override=config.RESEARCHER_MODEL_OVERRIDE)

    decompose_prompt = _DECOMPOSE_SYSTEM_PROMPT.format(n=config.MAX_RESEARCH_QUERIES)
    response = llm.invoke([
        ("system", decompose_prompt),
        ("human", f"Topic: {state['topic']}"),
    ])
    try:
        sub_queries = json.loads(response.content.strip())
        if not isinstance(sub_queries, list) or not sub_queries:
            raise ValueError("empty or non-list response")
    except (json.JSONDecodeError, ValueError):
        # Fall back to a single query using the raw topic -- degraded but
        # not a failure. A malformed decomposition shouldn't abort the whole
        # research pass when searching the raw topic still produces useful results.
        sub_queries = [state["topic"]]

    search_results = run_multi_search(sub_queries)

    results_text = "\n\n".join(
        f"Query: {r.query}\n" + "\n".join(
            f"- {item['title']} [{item['url']}]: {item['content'][:300]}"
            for item in r.results
        )
        for r in search_results
    )

    synthesis = llm.invoke([
        ("system", _SYNTHESIZE_SYSTEM_PROMPT),
        ("human", f"Topic: {state['topic']}\n\nRAW SEARCH RESULTS:\n{results_text}"),
    ])

    all_sources = [
        {"title": item["title"], "url": item["url"]}
        for r in search_results
        for item in r.results
    ]

    return {
        "sub_queries": sub_queries,
        "research_notes": synthesis.content,
        "sources": all_sources,
        "status": "researched",
    }


@traceable(name="agent.analyst", run_type="chain")
def analyst_node(state: dict) -> dict:
    llm = get_llm(temperature=0.1, model_override=config.ANALYST_MODEL_OVERRIDE)
    response = llm.invoke([
        ("system", _ANALYSIS_SYSTEM_PROMPT),
        ("human", f"Topic: {state['topic']}\n\nRESEARCH NOTES:\n{state['research_notes']}"),
    ])
    return {"analysis": response.content, "status": "analyzed"}


@traceable(name="agent.writer", run_type="chain")
def writer_node(state: dict) -> dict:
    llm = get_llm(temperature=0.4, model_override=config.WRITER_MODEL_OVERRIDE)

    if state.get("revision_feedback"):
        # Revision pass: rewrite the existing draft based on feedback.
        response = llm.invoke([
            ("system", _REVISION_SYSTEM_PROMPT),
            ("human", (f"CURRENT DRAFT:\n{state['draft']}\n\n"
                       f"REVIEWER FEEDBACK:\n{state['revision_feedback']}")),
        ])
    else:
        # First pass: write from scratch based on the analysis.
        response = llm.invoke([
            ("system", _WRITER_SYSTEM_PROMPT),
            ("human", f"Topic: {state['topic']}\n\nANALYSIS:\n{state['analysis']}"),
        ])

    return {
        "draft": response.content,
        "revision_feedback": "",  # consumed -- clear it so the next pass starts fresh
        "status": "drafted",
    }
