# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync --frozen                              # install deps (Python >=3.13, uv-managed)
uv run uvicorn app.main:app --reload          # dev server on :8000
uv run ruff check .                           # lint (CI runs this before tests)
uv run pytest tests/ -v                       # full suite (fully mocked, no real API calls)
uv run pytest tests/test_agents.py::test_researcher_falls_back_to_single_query_on_bad_json -v   # single test
```

CI (`.github/workflows/ci.yml`) runs `ruff check .` then `pytest tests/ -v` with placeholder
API keys in env. Since validation moved out of import time (see `app/config.py` below), a
bare `import app.*` no longer needs keys — but `TestClient(app)` enters the lifespan, which
validates, so the placeholders are still required for the API tests.

## Architecture

A FastAPI service wrapping a LangGraph state machine. Request flow:

`researcher → analyst → writer → human_review (interrupt) → [approved | revise] → finalize`

- **`app/graph.py`** — the state machine. `ResearchState` (TypedDict) is the single
  contract passed between all nodes; adding a field means updating the initial-state dict in
  `main.py:start_research` too. `human_review_node` calls LangGraph's `interrupt()`, which
  genuinely suspends execution — the process returns to serving other requests and a later,
  separate HTTP call resumes the thread via `Command(resume=...)`. `route_after_review`
  force-finalizes past `MAX_REVISIONS` (3, module constant, not env-configurable) rather than
  looping indefinitely.
- **`app/agents.py`** — the three node functions plus all prompts. Plain
  `dict → dict` functions with no graph knowledge, so tests can call them directly.
  `writer_node` branches on `state["revision_feedback"]`: non-empty means rewrite the
  existing draft, empty means first draft from the analysis; it clears the field after
  consuming it.
- **`app/main.py`** — endpoints, auth, rate limiting, structured JSON logging to
  `logs/requests.log`. The graph is compiled during FastAPI's **lifespan startup** and stored on
  `app.state.graph`, *not* at module import — this is deliberate so tests can patch the agent
  nodes before `build_graph()` runs (see `tests/conftest.py`). Preserve this if you touch
  startup. `get_state` is wrapped in `asyncio.to_thread` because LangGraph's API is
  synchronous. **`graph.invoke` no longer runs inside the request** — both POSTs schedule
  `_run_graph` as a `BackgroundTask` and return `202` so the client can poll real per-node
  progress. Consequence: a node raising can no longer become a 500 on the POST, so
  `_run_graph` must never propagate; failures land in the in-process `_jobs` dict and
  surface through the polled `error` field. `_jobs` is lost on restart (same tradeoff as
  `metrics.py`) — the checkpoint itself is durable, only the running/error flag is not.
- **`app/config.py`** — every module reads settings from here, never `os.getenv` directly.
  Required keys are checked by `validate_llm_config()` / `validate_search_config()`, called
  during FastAPI's **lifespan startup** (`main.py:55-56`) and again inside `get_llm()`
  (`providers.py`) and `_get_search_tool()` (`tools.py`). Import stays side-effect-free, so
  `uvicorn --reload` survives a keyless box and tests can import `app.*` without env keys.
  A misconfigured deployment therefore fails at **startup**, not on the first request —
  don't move these back to import time.
- **`app/providers.py`** — `get_llm(temperature, model_override)` factory, `lru_cache`d.
  Swaps Groq/Vertex AI by `MODEL_PROVIDER`. Per-agent model pinning
  (`RESEARCHER_MODEL_OVERRIDE` etc.) is a `.env` change, not a code change.
- **`app/tools.py`** — Tavily wrapper. `run_multi_search` fans the sub-queries out across a
  `ThreadPoolExecutor` (blocking I/O, so the GIL is released per request) and returns results
  **in input order** — callers pair a result back to the sub-query that produced it, so keep
  `pool.map`, not `as_completed`. It also isolates per-query failures: a
  failed search returns an empty `SearchResult` with the error in `answer` rather than
  aborting the research pass.
- **`app/metrics.py`** — in-process counters behind a lock, exposed at `/metrics`. Resets on
  restart; not aggregated across instances.

### Checkpointing

`SqliteSaver` against `config.DB_PATH` (default `checkpoints.sqlite`, gitignored), opened as a
context manager for the app's lifetime in `lifespan`. Setting `DB_PATH=":memory:"` switches to
`MemorySaver` — `tests/conftest.py` does exactly this at import time. Thread state persists
across restarts, so a paused report can be resumed much later.

Note: `graph.py` exposes a `compile_graph(checkpointer)` helper that `main.py` does not
currently use (it calls `build_graph().compile(...)` inline).

### Fail-open behavior

Two deliberate degradations, both tested explicitly — don't "fix" them into hard failures
without cause: unparseable JSON from the researcher's query-decomposition call falls back to a
single search on the raw topic, and individual Tavily query failures are swallowed per-query.

## Endpoints

`POST /research` (**202**, returns `thread_id` immediately with `running: true`; the graph
runs in a FastAPI `BackgroundTask` and the client polls for progress) ·
`GET /research/{thread_id}` (poll target; `awaiting_review` requires `snapshot.next`
non-empty **and** no run in flight, because `next` is also non-empty mid-run) ·
`POST /research/{thread_id}/review` (**202**, `{approved, feedback}`; a revision re-runs the
writer in the background, `409` if a run is already in flight) · `/health` · `/ready` ·
`/metrics` · `/` serves `app/static/index.html` (the Web Studio UI), falling back to `/docs`.

Auth is opt-in: `X-API-Key` is only enforced when `API_KEY` is set in config, so local dev
runs unauthenticated by default.

## Testing conventions

No real API calls anywhere. `tests/conftest.py` provides `mocked_client` (patches
`app.graph.researcher_node` / `analyst_node` / `writer_node` — patch them **on `app.graph`**,
which is where `build_graph` resolves them — then enters `TestClient(app)` so lifespan runs
inside the patch) and `failing_researcher_client` for the error path. Agent-level tests patch
`app.agents.get_llm` and `app.agents.run_multi_search` and call the node functions directly.

## Known state

`REVIEW_TIMEOUT_MINUTES` is config-only and unenforced — no eviction job exists.

`app/static/index.html` vendors its JS in `app/static/vendor/` with the version in the
filename and the source URL in a comment. That is the update path: there is no Node
toolchain here. Report markdown is LLM-authored from scraped pages, so it goes through
`DOMPurify.sanitize()` before touching the DOM, and source URLs are gated to http/https.
Don't reintroduce a CDN `<script>` or an `innerHTML` template for either.
