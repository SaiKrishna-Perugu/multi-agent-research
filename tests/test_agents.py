"""Tests for individual agent node logic, isolated from the graph/API."""
import json
from unittest.mock import MagicMock, patch

import pytest

from app.agents import researcher_node, writer_node
from app.tools import SearchResult


def _fake_llm(content: str):
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content=content)
    return llm


def test_researcher_falls_back_to_single_query_on_bad_json():
    """If the query-decomposition LLM call returns unparseable JSON, the
    researcher should degrade to a single search using the raw topic --
    not crash the whole request."""
    bad_decompose_llm = _fake_llm("not valid json, sorry")

    with patch("app.agents.get_llm", return_value=bad_decompose_llm), \
         patch("app.agents.run_multi_search", return_value=[
             SearchResult(query="fallback topic", results=[], answer="")
         ]) as mock_search:
        result = researcher_node({"topic": "fallback topic"})

    mock_search.assert_called_once_with(["fallback topic"])
    assert result["status"] == "researched"


def test_researcher_uses_decomposed_queries_when_valid():
    # First get_llm() call is for decomposition, second is for synthesis --
    # both go through the same mocked provider in this test, so we return
    # the decompose response first, then swap for synthesis via side_effect.
    llm = MagicMock()
    llm.invoke.side_effect = [
        MagicMock(content=json.dumps(["query one", "query two"])),
        MagicMock(content="synthesized notes"),
    ]

    with patch("app.agents.get_llm", return_value=llm), \
         patch("app.agents.run_multi_search", return_value=[
             SearchResult(query="query one", results=[{"title": "A", "url": "u1", "content": "c1"}]),
             SearchResult(query="query two", results=[{"title": "B", "url": "u2", "content": "c2"}]),
         ]) as mock_search:
        result = researcher_node({"topic": "some topic"})

    mock_search.assert_called_once_with(["query one", "query two"])
    assert result["research_notes"] == "synthesized notes"
    assert len(result["sources"]) == 2


def test_writer_first_pass_uses_analysis_not_feedback():
    llm = _fake_llm("a fresh draft")
    with patch("app.agents.get_llm", return_value=llm):
        result = writer_node({"topic": "t", "analysis": "the analysis", "revision_feedback": ""})

    assert result["draft"] == "a fresh draft"
    assert result["revision_feedback"] == ""  # cleared/empty on a first pass too


def test_writer_revision_pass_incorporates_feedback():
    llm = _fake_llm("a revised draft")
    with patch("app.agents.get_llm", return_value=llm):
        result = writer_node({
            "topic": "t", "analysis": "the analysis",
            "draft": "old draft", "revision_feedback": "make it shorter",
        })

    assert result["draft"] == "a revised draft"
    assert result["revision_feedback"] == ""  # consumed after use
    # Confirm the revision prompt path was taken, not the first-pass path,
    # by checking the human message passed to the LLM referenced the feedback.
    call_args = llm.invoke.call_args[0][0]
    human_message = call_args[1][1]
    assert "make it shorter" in human_message
    assert "old draft" in human_message


def test_researcher_surfaces_missing_llm_config_as_runtime_error(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "MODEL_PROVIDER", "groq")
    monkeypatch.setattr(config, "GROQ_API_KEY", "")

    with pytest.raises(RuntimeError, match="GROQ_API_KEY is not set"):
        researcher_node({"topic": "fallback topic"})
