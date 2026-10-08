"""
Web search tool for the researcher agent, via Tavily -- a search API built
specifically for LLM agents (returns clean, summarized results rather than
raw HTML/SERP scraping).
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from langchain_tavily import TavilySearch

from app import config
from app.typesafe_client import evaluate_system_one

logger = logging.getLogger("tools")


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
    doesn't abort the whole research pass; a partial result set is more
    useful to the researcher node than no result at all."""
    if not queries:
        return []

    def _safe(query: str) -> SearchResult:
        try:
            return run_search(query)
        except Exception as exc:
            return SearchResult(
                query=query, results=[], answer=f"[search failed: {exc}]"
            )

    # map() preserves input order, which callers rely on when pairing a result
    # back to the sub-query that produced it.
    with ThreadPoolExecutor(max_workers=min(len(queries), 8)) as pool:
        return list(pool.map(_safe, queries))


def audit_citations(draft: str, sources: list[dict], revision_count: int = 0) -> dict:
    """Audit inline markdown citations against retrieved sources.
    Extracts [text](url) links, checks scheme validity, and flags ungrounded URLs.
    When TypeSafe is available, performs semantic grounding verification on claims."""
    import re
    from urllib.parse import urlparse

    link_pattern = r"\[([^\]]+)\]\((https?://[^\s\)]+)\)"
    found_links = re.findall(link_pattern, draft)

    known_urls = {
        s.get("url", "").rstrip("/")
        for s in sources
        if isinstance(s, dict) and s.get("url")
    }
    grounded = []
    ungrounded = []
    valid_links = []

    for text, url in found_links:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            ungrounded.append({"text": text, "url": url, "reason": "invalid_url"})
            continue
        valid_links.append((text, url))

    # If TypeSafe is available and we have valid links and sources with content, perform semantic verification
    has_content = any(isinstance(s, dict) and s.get("content") for s in sources)
    if config.is_typesafe_available() and valid_links and sources and has_content:
        try:
            from typesafe_sdk import Choice

            eval_state = {
                "claims": [{"claim": text, "url": url} for text, url in valid_links],
                "sources": [
                    {
                        "url": s.get("url", ""),
                        "title": s.get("title", ""),
                        "content": s.get("content", "")[:1200],
                    }
                    for s in sources
                    if isinstance(s, dict)
                ],
            }
            questions = {
                f"citation_{idx}": Choice(
                    instructions=(
                        f"Does the retrieved source material support the claim: '{text}' (cited at {url})?"
                    ),
                    criteria={
                        "supported": "Source material clearly mentions and verifies the claim",
                        "unsupported": "Source material does not mention or verify this claim",
                        "contradicted": "Source material directly disputes or contradicts the claim",
                    },
                )
                for idx, (text, url) in enumerate(valid_links)
            }
            res = evaluate_system_one(eval_state, questions)
            if res and hasattr(res, "choices") and res.choices:
                for idx, (text, url) in enumerate(valid_links):
                    q_id = f"citation_{idx}"
                    choice_ans = res.choices.get(q_id)
                    choice = choice_ans.choice if choice_ans else None
                    conf = getattr(choice_ans, "confidence", 1.0)
                    if choice == "supported":
                        grounded.append({"text": text, "url": url, "confidence": conf})
                    elif choice == "contradicted":
                        ungrounded.append(
                            {
                                "text": text,
                                "url": url,
                                "reason": "contradicted_by_source",
                                "confidence": conf,
                            }
                        )
                    else:
                        ungrounded.append(
                            {
                                "text": text,
                                "url": url,
                                "reason": "unsupported_by_source",
                                "confidence": conf,
                            }
                        )
                total = len(found_links)
                precision = round(len(grounded) / total, 4) if total > 0 else None
                return {
                    "total_citations": total,
                    "grounded_count": len(grounded),
                    "ungrounded_count": len(ungrounded),
                    "precision": precision,
                    "grounded": grounded,
                    "ungrounded": ungrounded,
                    "verifier": "typesafe",
                    "status": "evaluated" if total > 0 else "no_citations",
                    "revision_count": revision_count,
                }
        except Exception as exc:
            logging.getLogger("tools").warning(
                "TypeSafe citation audit error, falling back: %s", exc
            )

    # Fallback to URL matching
    for text, url in valid_links:
        clean_url = url.rstrip("/")
        if clean_url in known_urls:
            grounded.append({"text": text, "url": url})
        else:
            ungrounded.append({"text": text, "url": url, "reason": "unmatched_source"})

    total = len(found_links)
    precision = round(len(grounded) / total, 4) if total > 0 else None

    return {
        "total_citations": total,
        "grounded_count": len(grounded),
        "ungrounded_count": len(ungrounded),
        "precision": precision,
        "grounded": grounded,
        "ungrounded": ungrounded,
        "verifier": "url_match" if total > 0 else "none",
        "status": "evaluated" if total > 0 else "no_citations",
        "revision_count": revision_count,
    }
