"""
Web search tool for the researcher agent, via Tavily -- a search API built
specifically for LLM agents (returns clean, summarized results rather than
raw HTML/SERP scraping).
"""
from dataclasses import dataclass, field

from langchain_tavily import TavilySearch

from app import config


@dataclass
class SearchResult:
    query: str
    results: list = field(default_factory=list)  # list of {"title", "url", "content"}
    answer: str = ""  # Tavily's own quick-answer summary, if available


def _get_search_tool() -> TavilySearch:
    return TavilySearch(
        max_results=config.MAX_SEARCH_RESULTS,
        search_depth=config.SEARCH_DEPTH,
        include_answer=True,
    )


def run_search(query: str) -> SearchResult:
    """Run one search query, return structured results with sources."""
    tool = _get_search_tool()
    raw = tool.invoke({"query": query})

    # TavilySearch's invoke() returns a dict with "results" (list of
    # {title, url, content, score, ...}) and optionally "answer".
    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", ""),
        }
        for r in raw.get("results", [])
    ]
    return SearchResult(query=query, results=results, answer=raw.get("answer", ""))


def run_multi_search(queries: list) -> list:
    """Run several search queries (one per sub-topic) and return all results.
    Errors on an individual query are isolated -- one failed search doesn't
    abort the whole research pass, matching the same per-item error
    isolation philosophy used in rag-capstone's ingestion pipeline."""
    all_results = []
    for query in queries:
        try:
            all_results.append(run_search(query))
        except Exception as exc:
            all_results.append(SearchResult(query=query, results=[], answer=f"[search failed: {exc}]"))
    return all_results
