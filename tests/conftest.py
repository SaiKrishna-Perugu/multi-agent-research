"""
Shared fixtures. The `mocked_client` fixture patches the three agent node
functions with deterministic fakes, then builds the app fresh inside that
patch context -- this matters because app/main.py builds the LangGraph
graph during FastAPI's lifespan startup (not at module import time),
specifically so each test's patches are correctly picked up when
`build_graph()` runs again on every `with TestClient(app) as client:` entry.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import config

config.DB_PATH = ":memory:"


@pytest.fixture
def fake_agents():
    """Deterministic replacements for the three agent nodes. draft_counter
    is fixture-scoped (fresh per test) so revision numbering never leaks
    across tests."""
    draft_counter = {"n": 0}

    def fake_researcher(state):
        return {
            "sub_queries": ["q1", "q2"],
            "research_notes": "synthesized notes",
            "sources": [{"title": "Example Source", "url": "https://example.com"}],
            "status": "researched",
        }

    def fake_analyst(state):
        return {"analysis": "## Key Themes\n...\n## Gaps & Contradictions\n...", "status": "analyzed"}

    def fake_writer(state):
        draft_counter["n"] += 1
        return {"draft": f"draft v{draft_counter['n']}", "revision_feedback": "", "status": "drafted"}

    return {"researcher": fake_researcher, "analyst": fake_analyst, "writer": fake_writer}


@pytest.fixture
def mocked_client(fake_agents):
    with patch("app.graph.researcher_node", fake_agents["researcher"]), \
         patch("app.graph.analyst_node", fake_agents["analyst"]), \
         patch("app.graph.writer_node", fake_agents["writer"]):
        from app.main import app
        with TestClient(app) as client:
            yield client


@pytest.fixture
def failing_researcher_client():
    """A variant where the researcher node itself raises -- used to test
    that /research surfaces a 500 with a logged error instead of crashing
    the process or leaking a stack trace to the caller."""
    def broken_researcher(state):
        raise RuntimeError("simulated researcher failure")

    with patch("app.graph.researcher_node", broken_researcher):
        from app.main import app
        with TestClient(app) as client:
            yield client
