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
from collections import OrderedDict
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
from pydantic import BaseModel, Field, field_validator, model_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app import config, metrics
from app.graph import MAX_REVISIONS, build_graph
from app.typesafe_client import classify_runtime_error

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
allow_all_origins = "*" in config.CORS_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=not allow_all_origins,
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

    @field_validator("topic")
    @classmethod
    def validate_topic(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Topic cannot be empty or whitespace only.")
        return stripped


class ReviewRequest(BaseModel):
    approved: bool
    feedback: str = Field(default="", max_length=2000)
    action: str = ""  # "approve", "revise", or "research_gap"
    review_version: str = ""

    @field_validator("feedback")
    @classmethod
    def validate_feedback(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def validate_action_consistency(self) -> "ReviewRequest":
        if self.action and self.action not in {"approve", "revise", "research_gap"}:
            raise ValueError(
                f"Invalid review action: '{self.action}'. Must be 'approve', 'revise', or 'research_gap'."
            )
        if self.approved and self.action in {"revise", "research_gap"}:
            raise ValueError(
                "Contradictory review decision: approved is True but revision action was requested."
            )
        if not self.approved and self.action == "approve":
            raise ValueError(
                "Contradictory review decision: approved is False but action is 'approve'."
            )
        return self


# --- Background job tracking ---------------------------------------------------
# The graph runs in a background task so POST can return a thread_id immediately
# and the client can poll for real per-node progress. A background exception has
# no HTTP response waiting to carry it, so it lands here instead.
#
# In-process and lost on restart -- same tradeoff as metrics.py. The checkpoint
# itself is durable in SQLite; only the "is it running / did it blow up" flag is
# not. A restart mid-run leaves a thread paused at its last completed node.
class BoundedJobStore:
    """Bounded, thread-safe store for background task execution state and error flags.

    Separates active running claims from bounded finished error history so that
    error history eviction can NEVER evict or cancel an active running job.
    Enforces admission limits (max_active_runs) to prevent runaway concurrency.
    """

    def __init__(self, maxsize: int = 1000, max_active_runs: int = 1):
        self._maxsize = maxsize
        self._max_active_runs = max_active_runs
        self._lock = threading.Lock()
        self._active: dict[str, float] = {}
        self._errors: OrderedDict[str, str] = OrderedDict()

    def try_claim(self, thread_id: str) -> str:
        """Attempt to claim execution slot for a thread.
        Returns:
            'ok' if successfully claimed.
            'already_running' if this thread is already running.
            'overloaded' if active run capacity has been reached.
        """
        with self._lock:
            if thread_id in self._active:
                return "already_running"
            if len(self._active) >= self._max_active_runs:
                return "overloaded"
            self._active[thread_id] = time.perf_counter()
            self._errors.pop(thread_id, None)
            return "ok"

    def release_claim(self, thread_id: str) -> None:
        """Release an active claim without recording an error."""
        with self._lock:
            self._active.pop(thread_id, None)

    def finish(self, thread_id: str, error: str = "") -> None:
        with self._lock:
            self._active.pop(thread_id, None)
            if error:
                self._errors[thread_id] = error
                self._errors.move_to_end(thread_id)
                while len(self._errors) > self._maxsize:
                    self._errors.popitem(last=False)
            else:
                self._errors.pop(thread_id, None)

    def state(self, thread_id: str) -> dict:
        with self._lock:
            return {
                "running": thread_id in self._active,
                "error": self._errors.get(thread_id, ""),
            }

    def __contains__(self, thread_id: object) -> bool:
        with self._lock:
            return thread_id in self._active or thread_id in self._errors

    def pop(self, thread_id: str, default=None):
        with self._lock:
            val = self._active.pop(thread_id, None)
            err = self._errors.pop(thread_id, None)
            if val is not None or err is not None:
                return {"running": val is not None, "error": err or ""}
            return default

    def clear(self) -> None:
        with self._lock:
            self._active.clear()
            self._errors.clear()


_jobs = BoundedJobStore(maxsize=1000, max_active_runs=config.MAX_ACTIVE_RUNS)


def _job_try_claim(thread_id: str) -> str:
    return _jobs.try_claim(thread_id)


def _job_release_claim(thread_id: str) -> None:
    _jobs.release_claim(thread_id)


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
        category = classify_runtime_error(exc) if config.is_typesafe_available() else ""
        error_msg = (
            f"Report generation failed [{category}]: {exc}"
            if category and category != "system_error"
            else f"Report generation failed: {exc}"
        )
        logger.error(
            json.dumps(
                {
                    "event": "error",
                    "thread_id": thread_id,
                    "error": str(exc),
                    "error_category": category or "uncategorized",
                }
            )
        )
        _job_finish(thread_id, error_msg)
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
    review_version: str = ""


def _state_to_response(
    thread_id: str,
    state: dict,
    interrupted: bool,
    *,
    running: bool = False,
    error: str = "",
) -> ResearchResponse:
    audit = state.get("citation_audit")
    if not audit:
        audit = {
            "status": "not_evaluated",
            "verifier": "none",
            "total_citations": 0,
            "grounded_count": 0,
            "ungrounded_count": 0,
            "precision": None,
            "grounded": [],
            "ungrounded": [],
        }
    review_version = f"v{state.get('revision_count', 0)}" if interrupted else ""
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
        sources=state.get("sources", []),
        awaiting_review=interrupted,
        citation_audit=audit,
        review_version=review_version,
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
    claim_status = _job_try_claim(thread_id)
    if claim_status == "overloaded":
        raise HTTPException(
            status_code=429,
            detail=f"System is at capacity. Only {config.MAX_ACTIVE_RUNS} active research run allowed at a time.",
        )
    if claim_status != "ok":
        raise HTTPException(
            status_code=409,
            detail="This research thread is already running.",
        )

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
        "citation_audit": {},
    }

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
    claim_status = _job_try_claim(thread_id)
    if claim_status == "overloaded":
        raise HTTPException(
            status_code=429,
            detail=f"System is at capacity. Only {config.MAX_ACTIVE_RUNS} active research run allowed at a time.",
        )
    if claim_status != "ok":
        raise HTTPException(
            status_code=409,
            detail="This report is still being generated. Wait for it to finish.",
        )

    try:
        thread_config = {"configurable": {"thread_id": thread_id}}
        snapshot = await asyncio.to_thread(
            request.app.state.graph.get_state, thread_config
        )
        if not snapshot.values:
            raise HTTPException(
                status_code=404, detail=f"No research thread found for id {thread_id}"
            )

        if "human_review" not in snapshot.next:
            raise HTTPException(
                status_code=400,
                detail="This report is not awaiting review (already finalized, failed, or not yet started).",
            )

        if _is_review_timed_out(snapshot):
            raise HTTPException(
                status_code=400,
                detail=f"Review window ({config.REVIEW_TIMEOUT_MINUTES} minutes) has expired for this report.",
            )

        expected_version = f"v{snapshot.values.get('revision_count', 0)}"
        if not body.review_version or body.review_version != expected_version:
            raise HTTPException(
                status_code=409,
                detail=f"Stale or missing review version '{body.review_version}'. Current review version is '{expected_version}'.",
            )

        if not body.approved:
            metrics.record_revision_requested()

        resume_payload = Command(
            resume={
                "approved": body.approved,
                "feedback": body.feedback,
                "action": body.action,
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
                    "review_version": body.review_version,
                }
            )
        )
        return _state_to_response(thread_id, snapshot.values, False, running=True)
    except Exception:
        _job_release_claim(thread_id)
        raise
