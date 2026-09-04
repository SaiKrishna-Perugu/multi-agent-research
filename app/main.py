"""
FastAPI service for the multi-agent research/report generator.

Endpoints:
  POST /research               -- start a new report (topic in, draft +
                                   thread_id out; pauses for human review)
  GET  /research/{thread_id}   -- check status / current draft
  POST /research/{thread_id}/review -- approve or request revisions
  GET  /health, /ready, /metrics

Run:
    uv run uvicorn app.main:app --reload
"""

import asyncio
import hmac
import json
import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app import config, metrics
from app.graph import MAX_REVISIONS, build_graph
from app.tools import audit_citations

# --- Structured logging ------------------------------------------------------
LOG_PATH = Path("logs")
LOG_PATH.mkdir(exist_ok=True)
logger = logging.getLogger("research_service")
logger.setLevel(logging.INFO)
_handler = logging.FileHandler(LOG_PATH / "requests.log")
_handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(_handler)
logger.addHandler(logging.StreamHandler())


# --- Graph lifecycle -----------------------------------------------------------
# Built during startup and stored on app.state (not a module-level global)
# so tests can patch agent nodes prior to build_graph(). Checkpointer is
# SqliteSaver by default for persistence across restarts/instances, or
# MemorySaver if config.DB_PATH == ":memory:".
@asynccontextmanager
async def lifespan(app: FastAPI):
    config.validate_llm_config()
    config.validate_search_config()
    if config.DB_PATH == ":memory:":
        app.state.graph = build_graph().compile(checkpointer=MemorySaver())
        yield
    else:
        with SqliteSaver.from_conn_string(config.DB_PATH) as checkpointer:
            app.state.graph = build_graph().compile(checkpointer=checkpointer)
            yield


def _get_client_identity(request: Request) -> str:
    """Identify client by API key, then first X-Forwarded-For IP, falling back to remote address."""
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"key:{api_key}"
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


# --- App setup -----------------------------------------------------------------
limiter = Limiter(key_func=_get_client_identity)
app = FastAPI(title="Multi-Agent Research API", version="0.1.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def require_api_key(x_api_key: str = Header(default="")) -> None:
    if config.API_KEY and not hmac.compare_digest(x_api_key, config.API_KEY):
        raise HTTPException(
            status_code=401, detail="Invalid or missing X-API-Key header"
        )


class ResearchRequest(BaseModel):
    topic: str = Field(
        ...,
        min_length=1,
        max_length=500,
        examples=["The current state of small modular nuclear reactors"],
    )


class ReviewRequest(BaseModel):
    approved: bool
    feedback: str = ""
    action: str = ""  # "approve", "revise", or "research_gap"


# --- Background job tracking ---------------------------------------------------
# The graph runs in a background task so POST can return a thread_id immediately
# and the client can poll for real per-node progress. A background exception has
# no HTTP response waiting to carry it, so it lands here instead.
#
# In-process and lost on restart -- same tradeoff as metrics.py. The checkpoint
# itself is durable in SQLite; only the "is it running / did it blow up" flag is
# not. A restart mid-run leaves a thread paused at its last completed node,
class BoundedJobStore(dict):
    """Bounded, thread-safe store for background task execution state and error flags."""

    def __init__(self, maxsize: int = 1000):
        super().__init__()
        self._maxsize = maxsize
        self._lock = threading.Lock()

    def start(self, thread_id: str) -> None:
        with self._lock:
            self[thread_id] = {"running": True, "error": ""}

    def try_claim(self, thread_id: str) -> bool:
        with self._lock:
            if self.get(thread_id, {}).get("running"):
                return False
            self[thread_id] = {"running": True, "error": ""}
            return True

    def finish(self, thread_id: str, error: str = "") -> None:
        with self._lock:
            if error:
                self[thread_id] = {"running": False, "error": error}
                if len(self) > self._maxsize:
                    oldest = next(iter(self))
                    self.pop(oldest, None)
            else:
                self.pop(thread_id, None)

    def state(self, thread_id: str) -> dict:
        with self._lock:
            return dict(self.get(thread_id, {"running": False, "error": ""}))


_jobs = BoundedJobStore(maxsize=1000)


def _job_start(thread_id: str) -> None:
    _jobs.start(thread_id)


def _job_try_claim(thread_id: str) -> bool:
    return _jobs.try_claim(thread_id)


def _job_finish(thread_id: str, error: str = "") -> None:
    _jobs.finish(thread_id, error)


def _job_state(thread_id: str) -> dict:
    return _jobs.state(thread_id)


def _is_review_timed_out(snapshot) -> bool:
    """Check if an awaiting-review thread has exceeded config.REVIEW_TIMEOUT_MINUTES."""
    if not getattr(snapshot, "created_at", None) or config.REVIEW_TIMEOUT_MINUTES <= 0:
        return False
    try:
        from datetime import datetime

        created_str = str(snapshot.created_at)
        if created_str.endswith("Z"):
            created_str = created_str[:-1] + "+00:00"
        created = datetime.fromisoformat(created_str)
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        elapsed_min = (datetime.now(UTC) - created).total_seconds() / 60
        return elapsed_min > config.REVIEW_TIMEOUT_MINUTES
    except Exception:
        return False


def _run_graph(graph, thread_id: str, payload, topic: str = "") -> None:
    """Execute one graph run to its next stop. Runs in a worker thread after the
    response has been sent, so it must never raise -- failures go to _jobs."""
    start = time.perf_counter()
    thread_config = {"configurable": {"thread_id": thread_id}}
    try:
        result = graph.invoke(payload, config=thread_config)
    except Exception as exc:
        metrics.record_request((time.perf_counter() - start) * 1000, error=True)
        logger.error(
            json.dumps({"event": "error", "thread_id": thread_id, "error": str(exc)})
        )
        _job_finish(thread_id, "Report generation failed. Check server logs.")
        return

    latency_ms = (time.perf_counter() - start) * 1000
    metrics.record_request(latency_ms)
    if result.get("status") == "finalized":
        metrics.record_report_finalized()
    logger.info(
        json.dumps(
            {
                "event": "graph_run_complete",
                "thread_id": thread_id,
                "topic": topic,
                "status": result.get("status"),
                "revision_count": result.get("revision_count"),
                "latency_ms": round(latency_ms),
            }
        )
    )
    _job_finish(thread_id)


class ResearchResponse(BaseModel):
    thread_id: str
    status: str
    topic: str = ""
    running: bool = False
    error: str = ""
    draft: str = ""
    final_report: str = ""
    revision_count: int
    sub_queries: list[str] = Field(default_factory=list)
    sources: list = Field(default_factory=list)
    awaiting_review: bool
    citation_audit: dict = Field(default_factory=dict)


def _state_to_response(
    thread_id: str,
    state: dict,
    interrupted: bool,
    *,
    running: bool = False,
    error: str = "",
) -> ResearchResponse:
    report_text = state.get("final_report") or state.get("draft") or ""
    sources = state.get("sources", [])
    audit = audit_citations(report_text, sources) if report_text and sources else {}
    return ResearchResponse(
        thread_id=thread_id,
        status=state.get("status", "unknown"),
        topic=state.get("topic", ""),
        running=running,
        error=error,
        draft=state.get("draft", ""),
        final_report=state.get("final_report", ""),
        revision_count=state.get("revision_count", 0),
        sub_queries=state.get("sub_queries", []),
        sources=sources,
        awaiting_review=interrupted,
        citation_audit=audit,
    )


@app.get("/", include_in_schema=False)
def root():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return RedirectResponse(url="/docs")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict:
    # Confirms the graph compiled and dependencies (Tavily/LLM provider
    # config) loaded without error at startup -- distinct from /health,
    # which only confirms the process is alive.
    model = (
        config.VERTEX_CHAT_MODEL
        if config.MODEL_PROVIDER == "vertexai"
        else config.GROQ_CHAT_MODEL
    )
    return {
        "status": "ready",
        "model_provider": config.MODEL_PROVIDER,
        "model": model,
        "max_revisions": MAX_REVISIONS,
    }


@app.get("/metrics", dependencies=[Depends(require_api_key)])
def get_metrics() -> dict:
    return metrics.get_metrics_snapshot()


@app.post(
    "/research",
    response_model=ResearchResponse,
    status_code=202,
    dependencies=[Depends(require_api_key)],
)
@limiter.limit(config.RATE_LIMIT)
async def start_research(
    request: Request, body: ResearchRequest, background: BackgroundTasks
) -> ResearchResponse:
    """Accept the topic and return a thread_id immediately (202). The graph runs
    in the background; poll GET /research/{thread_id} for per-node progress."""
    thread_id = str(uuid.uuid4())
    metrics.record_report_started()

    initial_state = {
        "topic": body.topic,
        "sub_queries": [],
        "research_notes": "",
        "sources": [],
        "analysis": "",
        "draft": "",
        "revision_feedback": "",
        "revision_count": 0,
        "final_report": "",
        "status": "started",
        "review_action": "",
    }

    _job_start(thread_id)
    background.add_task(
        _run_graph, request.app.state.graph, thread_id, initial_state, body.topic
    )

    logger.info(
        json.dumps(
            {
                "event": "research_accepted",
                "thread_id": thread_id,
                "topic": body.topic,
            }
        )
    )
    return _state_to_response(
        thread_id, {"topic": body.topic, "status": "started"}, False, running=True
    )


@app.get(
    "/research/{thread_id}",
    response_model=ResearchResponse,
    dependencies=[Depends(require_api_key)],
)
async def get_research(request: Request, thread_id: str) -> ResearchResponse:

    thread_config = {"configurable": {"thread_id": thread_id}}
    snapshot = await asyncio.to_thread(request.app.state.graph.get_state, thread_config)
    job = _job_state(thread_id)

    if not snapshot.values:
        # A thread accepted moments ago may not have checkpointed yet. That is
        # not a 404 -- the client is polling a thread_id we just handed it.
        if job["running"] or job["error"]:
            return _state_to_response(
                thread_id,
                {"status": "started"},
                False,
                running=job["running"],
                error=job["error"],
            )
        raise HTTPException(
            status_code=404, detail=f"No research thread found for id {thread_id}"
        )

    # snapshot.next being non-empty just means "some node runs next" -- true
    # for the initial pre-researcher state and mid-revision states too, not
    # just a genuine interrupt() pause. Checking that "human_review" is
    # specifically the next node pins this to the real pause point, and
    # because it reads the persisted checkpoint rather than the in-memory
    # _jobs dict, it stays correct across a restart that loses job["error"]
    # (e.g. the process crashed before a failure was ever recorded).
    interrupted = "human_review" in snapshot.next and not job["running"]
    timed_out = interrupted and _is_review_timed_out(snapshot)
    if timed_out:
        interrupted = False
        status = "review_timed_out"
        error = f"Review window ({config.REVIEW_TIMEOUT_MINUTES}m) expired."
    else:
        status = snapshot.values.get("status", "unknown")
        error = job["error"]

    return _state_to_response(
        thread_id,
        {**snapshot.values, "status": status},
        interrupted,
        running=job["running"],
        error=error,
    )


@app.post(
    "/research/{thread_id}/review",
    response_model=ResearchResponse,
    status_code=202,
    dependencies=[Depends(require_api_key)],
)
@limiter.limit(config.RATE_LIMIT)
async def review_research(
    request: Request, thread_id: str, body: ReviewRequest, background: BackgroundTasks
) -> ResearchResponse:
    """Accept the decision and return immediately (202). A revision runs the
    writer again, which is slow; poll GET /research/{thread_id} for progress."""
    thread_config = {"configurable": {"thread_id": thread_id}}
    snapshot = await asyncio.to_thread(request.app.state.graph.get_state, thread_config)
    if not snapshot.values:
        raise HTTPException(
            status_code=404, detail=f"No research thread found for id {thread_id}"
        )

    if "human_review" not in snapshot.next:
        # See get_research's comment: pinning to the specific next node (not
        # just "next is non-empty") is what makes this correct even after a
        # restart wipes job["error"] for a thread that failed before, or
        # during, an interrupt.
        raise HTTPException(
            status_code=400,
            detail="This report is not awaiting review (already finalized, failed, or not yet started).",
        )

    if _is_review_timed_out(snapshot):
        raise HTTPException(
            status_code=400,
            detail=f"Review window ({config.REVIEW_TIMEOUT_MINUTES} minutes) has expired for this report.",
        )

    if not _job_try_claim(thread_id):
        raise HTTPException(
            status_code=409,
            detail="This report is still being generated. Wait for it to finish.",
        )

    if not body.approved:
        metrics.record_revision_requested()

    action = body.action or ("approve" if body.approved else "revise")
    resume_payload = Command(
        resume={
            "approved": body.approved,
            "feedback": body.feedback,
            "action": action,
        }
    )
    background.add_task(
        _run_graph,
        request.app.state.graph,
        thread_id,
        resume_payload,
        snapshot.values.get("topic", ""),
    )

    logger.info(
        json.dumps(
            {
                "event": "review_accepted",
                "thread_id": thread_id,
                "approved": body.approved,
            }
        )
    )
    return _state_to_response(thread_id, snapshot.values, False, running=True)
