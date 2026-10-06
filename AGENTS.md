# Project guide

## Setup and commands

This is a Python >=3.13 FastAPI service managed with uv. Run these commands from
this directory, which contains `pyproject.toml`, `uv.lock`, and `.git`:

```powershell
uv sync --frozen
uv run uvicorn app.main:app --reload
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/ -v
```

Create `.env` from `.env.example` only if it does not already exist. Real
application startup needs Groq and Tavily credentials, or the configured Vertex
AI alternative for the LLM. Never print or commit secrets. Treat SQLite
checkpoints, logs, caches, and virtual environments as local runtime data.

For isolated test runs, use placeholder credentials and disable optional
external services before importing application modules:

```powershell
$env:MODEL_PROVIDER = "groq"
$env:GROQ_API_KEY = "gsk-test-placeholder-not-real"
$env:TAVILY_API_KEY = "tvly-test-placeholder-not-real"
$env:API_KEY = ""
$env:GCP_PROJECT_ID = ""
$env:ENABLE_TYPESAFE = "false"
$env:TYPESAFE_API_KEY = ""
$env:LANGSMITH_TRACING = "false"
$env:LANGCHAIN_TRACING_V2 = "false"
uv run pytest tests/ -v
```

These variables affect the current shell; use a dedicated test shell. Mock
external calls in tests. Do not use production credentials for verification.
CI also audits dependencies and builds and smoke-tests the Docker image; see
`.github/workflows/ci.yml` for the exact pipeline.

## Architecture

- `app/main.py`: FastAPI routes, request/response models, authentication, rate
  limits, logging, background job state, and graph lifecycle. `/` serves the
  Web Studio from `app/static/index.html`, with a fallback to `/docs`.
- `app/graph.py`: typed graph state and routing. The flow is researcher ->
  analyst -> writer -> human review -> finalization or another pass.
  Review uses LangGraph `interrupt()` and resumes via `Command(resume=...)`.
  Research-gap feedback can route back to the researcher. `MAX_REVISIONS`
  bounds the loop and forced finalization includes a note.
- `app/agents.py`: prompts and researcher, analyst, and writer implementations.
  Follow-up research preserves existing notes and sources. Optional TypeSafe
  judgments filter passages.
- `app/tools.py`: Tavily search, ordered concurrent queries, and citation audit.
- `app/typesafe_client.py`: optional semantic judgments for review intent,
  citation checks, passage filtering, and runtime error classification.
  Missing configuration or failed evaluations must retain fallback behavior.
- `app/config.py`: centralized environment settings, Secret Manager fallback,
  and configuration validators. `app/providers.py` creates and caches Groq or
  Vertex AI clients, including per-agent model overrides.
- `app/metrics.py`: in-process metrics exposed by `/metrics`.
- `tests/`: agent, API lifecycle, tools, and TypeSafe tests using mocks.

## Implementation conventions

Build and compile the graph during FastAPI lifespan startup so tests can patch
nodes before graph construction. Keep explicit credential validation at startup
and provider/tool call sites rather than moving it to module import.

The two research POST routes return HTTP 202 and run graph work in background
tasks. Preserve atomic job claims for concurrent reviews and expose background
failures through polled state. Awaiting-review status depends on the next graph
node being `human_review` and no run being active.

Read settings through `app.config`. If adding a graph state field, update
`ResearchState`, the initial state in `start_research`, and relevant response
models, mocks, and consumers. Preserve query result order and per-query failure
isolation. Invalid decomposition JSON intentionally falls back to a topic query.

The default checkpointer is SQLite for the lifespan of the application.
`DB_PATH=":memory:"` selects `MemorySaver`; the shared test fixtures use this.
Do not overwrite the local checkpoint database during tests. Patch agent nodes
on `app.graph` for API tests, and LLM/search dependencies on `app.agents` for
isolated agent tests.

The UI uses plain HTML, CSS, and JavaScript with vendored libraries; there is no
Node build step. Sanitize rendered report Markdown with DOMPurify and restrict
source links to HTTP/HTTPS. Preserve these protections when changing rendering.

## Operational boundaries and documentation

SQLite is local to an instance. Background job flags and metrics are process
local. Review timeout detection exists, but there is no automatic eviction job.
Do not describe this deployment as shared durable state across Cloud Run
instances.

`README.md`, `CLAUDE.md`, `.agents/AGENTS.md`, `PLAN.md`, and
`IMPLEMENTATION.md` contain useful context, but some descriptions predate the
current implementation. In particular, `/` serves the Web Studio and review
feedback can trigger new research. Verify behavior against current code and
tests when updating documentation.
