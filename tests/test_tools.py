"""Tests for the web search tool wrapper -- specifically per-query error isolation."""

from unittest.mock import patch

import pytest

from app.tools import SearchResult, run_multi_search, run_search


def test_run_multi_search_isolates_per_query_failures():
    """One failing search query must not abort the others -- same
    per-item error isolation philosophy as rag-capstone's ingestion."""

    def fake_run_search(query):
        if query == "bad query":
            raise RuntimeError("search API error")
        return SearchResult(
            query=query, results=[{"title": "ok", "url": "u", "content": "c"}]
        )

    with patch("app.tools.run_search", side_effect=fake_run_search):
        results = run_multi_search(["good query", "bad query", "another good query"])

    assert (
        len(results) == 3
    )  # all three queries produced a result, none aborted the batch
    assert results[0].results  # good query succeeded normally
    assert results[1].results == []  # bad query failed gracefully
    assert "search failed" in results[1].answer
    assert results[2].results  # third query still ran despite the second one failing


def test_run_search_returns_structured_results():
    from unittest.mock import MagicMock

    fake_tool = MagicMock()
    fake_tool.invoke.return_value = {
        "results": [{"title": "T", "url": "https://x.com", "content": "some content"}],
        "answer": "quick summary",
    }
    with patch("app.tools._get_search_tool", return_value=fake_tool):
        result = run_search("test query")

    assert result.query == "test query"
    assert result.answer == "quick summary"
    assert result.results[0]["title"] == "T"


def test_run_search_validates_tavily_config_at_runtime(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "TAVILY_API_KEY", "")

    with pytest.raises(RuntimeError, match="TAVILY_API_KEY is not set"):
        run_search("test query")


def test_audit_citations_validates_links_and_flags_hallucinations():
    from app.tools import audit_citations

    # Empty draft returns 1.0 precision and empty lists
    empty_result = audit_citations("No links here.", [])
    assert empty_result["total_citations"] == 0
    assert empty_result["precision"] == 1.0
    assert empty_result["grounded"] == []
    assert empty_result["ungrounded"] == []

    # Mixed draft with grounded, trailing-slash matching, and ungrounded links
    draft = (
        "According to research ([Study A](https://source.com/a)), "
        "growth reached 15% ([Report B](https://source.com/b/)). "
        "However, some claim 50% ([Fake Link](https://hallucinated.com/item))."
    )
    sources = [
        {"title": "Study A", "url": "https://source.com/a"},
        {"title": "Report B", "url": "https://source.com/b"},
    ]

    result = audit_citations(draft, sources)
    assert result["total_citations"] == 3
    assert result["grounded_count"] == 2
    assert result["ungrounded_count"] == 1
    assert result["precision"] == 0.6667
    assert len(result["grounded"]) == 2
    assert result["ungrounded"][0]["url"] == "https://hallucinated.com/item"
    assert result["ungrounded"][0]["reason"] == "unmatched_source"
