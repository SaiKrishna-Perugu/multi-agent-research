"""Provider deadlines tested through the real Vertex adapter, without API calls."""

from unittest.mock import MagicMock, patch

import pytest
from google.auth.credentials import AnonymousCredentials
from langchain_google_vertexai import ChatVertexAI

from app import config
from app.providers import get_llm


@pytest.fixture
def vertex_provider(monkeypatch):
    monkeypatch.setattr(config, "MODEL_PROVIDER", "vertexai")
    monkeypatch.setattr(config, "GCP_PROJECT_ID", "test-project")
    monkeypatch.setattr(config, "GCP_LOCATION", "global")
    monkeypatch.setattr(config, "VERTEX_CHAT_MODEL", "gemini-3.5-flash")
    monkeypatch.setattr(config, "LLM_REQUEST_TIMEOUT", 7)
    monkeypatch.setattr(config, "LLM_MAX_RETRIES", 0)

    def offline_vertex(**kwargs):
        return ChatVertexAI(credentials=AnonymousCredentials(), **kwargs)

    with patch("langchain_google_vertexai.ChatVertexAI", side_effect=offline_vertex):
        yield get_llm(temperature=0.2)


def test_vertex_timeout_reaches_prediction_request(vertex_provider):
    # A constructor mock would accept unknown kwargs and miss the original bug.
    # Exercise the real adapter's invoke path up to the network boundary.
    vertex_provider.client = MagicMock()
    vertex_provider.client.generate_content.side_effect = TimeoutError("deadline")

    with pytest.raises(TimeoutError, match="deadline"):
        vertex_provider.invoke("Say hello")

    call = vertex_provider.client.generate_content.call_args
    assert call.kwargs["timeout"] == 7
    assert call.kwargs["retry"] is None


def test_vertex_timeout_reaches_structured_output_request(vertex_provider):
    from app.agents import DecomposedQueries

    vertex_provider.client = MagicMock()
    vertex_provider.client.generate_content.side_effect = TimeoutError("deadline")

    with pytest.raises(TimeoutError, match="deadline"):
        vertex_provider.with_structured_output(DecomposedQueries).invoke("Find queries")

    assert vertex_provider.client.generate_content.call_args.kwargs["timeout"] == 7


def test_vertex_model_override_retains_deadline(vertex_provider):
    # Reuse the offline credential setup but build a distinct cached model.
    llm = get_llm(temperature=0.4, model_override="gemini-3.5-flash-lite")
    assert llm.model_name == "gemini-3.5-flash-lite"
    assert llm.timeout == 7
    assert llm.project == "test-project"
    assert llm.location == "global"
