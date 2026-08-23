"""
Web search tool for the researcher agent, via Tavily -- a search API built
specifically for LLM agents (returns clean, summarized results rather than
raw HTML/SERP scraping).
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from langchain_tavily import TavilySearch

from app import config


@dataclass
class SearchResult:
    query: str
    results: list = field(default_factory=list)  # list of {"title", "url", "content"}
    answer: str = ""  # Tavily's own quick-answer summary, if available


def _get_search_tool() -> TavilySearch:
    config.validate_search_config()
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
    """Run several search queries (one per sub-topic) concurrently and return
    all results, in the same order as `queries`.

    The searches are independent network calls, so running them serially made
    the researcher node the slowest part of a report by a wide margin. A thread
    pool is the right tool here: `TavilySearch.invoke` is blocking I/O, so the
    GIL is released while each request is in flight.

    Errors on an individual query are still isolated -- one failed search
    doesn't abort the whole research pass, matching the same per-item error
    isolation philosophy used in rag-capstone's ingestion pipeline."""
    if not queries:
        return []

    def _safe(query: str) -> SearchResult:
        try:
            return run_search(query)
        except Exception as exc:
            return SearchResult(query=query, results=[], answer=f"[search failed: {exc}]")

    # map() preserves input order, which callers rely on when pairing a result
    # back to the sub-query that produced it.
    with ThreadPoolExecutor(max_workers=min(len(queries), 8)) as pool:
        return list(pool.map(_safe, queries))
