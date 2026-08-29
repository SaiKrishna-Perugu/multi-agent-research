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


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    # app.main.app (and its slowapi Limiter) is a module-level singleton, so
    # its in-memory request counts otherwise carry over between tests. Without
    # this, a test earlier in the file can tip a later, unrelated test over
    # RATE_LIMIT and fail it with a 429.
    from app.main import limiter

    limiter.reset()
    yield


@pytest.fixture(autouse=True)
def _reset_llm_cache():
    # app.providers._build_llm is lru_cache'd at module scope. A test that
    # exercises the real provider (e.g. one asserting validate_llm_config()
    # raises for a given (provider, temperature, model_override) key) would
    # silently stop testing anything the moment that exact key is ever cached
    # from a prior successful call.
    from app.providers import _build_llm

    _build_llm.cache_clear()
    yield


@pytest.fixture(autouse=True)
def _reset_jobs():
    # app.main._jobs is a module-level dict, same singleton-module hazard as
    # the rate limiter and the LLM cache above. Thread_ids are UUIDs so a
    # leftover entry can't collide with another test today, but clearing it
    # keeps this fixture's guarantee ("every test starts from a clean slate")
    # actually true rather than true by coincidence.
    from app.main import _jobs

    _jobs.clear()
    yield


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
    """A variant where the researcher node itself raises -- used to test that
    the background run logs the error and never crashes the process, and that
    the failure surfaces via the polled GET's `error` field rather than a 500
    on the original POST (which returns 202 before the graph ever runs)."""
    def broken_researcher(state):
        raise RuntimeError("simulated researcher failure")

    with patch("app.graph.researcher_node", broken_researcher):
        from app.main import app
        with TestClient(app) as client:
            yield client
