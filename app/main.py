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
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
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
    if config.DB_PATH == ":memory:":
        app.state.graph = build_graph().compile(checkpointer=MemorySaver())
        yield
    else:
        with SqliteSaver.from_conn_string(config.DB_PATH) as checkpointer:
            app.state.graph = build_graph().compile(checkpointer=checkpointer)
            yield


# --- App setup -----------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)
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
    if config.API_KEY and x_api_key != config.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")


class ResearchRequest(BaseModel):
    topic: str = Field(..., min_length=1, examples=["The current state of small modular nuclear reactors"])


class ReviewRequest(BaseModel):
    approved: bool
    feedback: str = ""


class ResearchResponse(BaseModel):
    thread_id: str
    status: str
    draft: str = ""
    final_report: str = ""
    revision_count: int
    sub_queries: list[str] = []
    sources: list = []
    awaiting_review: bool


def _state_to_response(thread_id: str, state: dict, interrupted: bool) -> ResearchResponse:
    return ResearchResponse(
        thread_id=thread_id,
        status=state.get("status", "unknown"),
        draft=state.get("draft", ""),
        final_report=state.get("final_report", ""),
        revision_count=state.get("revision_count", 0),
        sub_queries=state.get("sub_queries", []),
        sources=state.get("sources", []),
        awaiting_review=interrupted,
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
    return {"status": "ready", "model_provider": config.MODEL_PROVIDER, "max_revisions": MAX_REVISIONS}


@app.get("/metrics")
def get_metrics() -> dict:
    return metrics.get_metrics_snapshot()


@app.post("/research", response_model=ResearchResponse, dependencies=[Depends(require_api_key)])
@limiter.limit(config.RATE_LIMIT)
async def start_research(request: Request, body: ResearchRequest) -> ResearchResponse:

    thread_id = str(uuid.uuid4())
    metrics.record_report_started()
    start = time.perf_counter()

    initial_state = {
        "topic": body.topic, "sub_queries": [], "research_notes": "", "sources": [],
        "analysis": "", "draft": "", "revision_feedback": "", "revision_count": 0,
        "final_report": "", "status": "started",
    }
    thread_config = {"configurable": {"thread_id": thread_id}}

    try:
        result = await asyncio.to_thread(request.app.state.graph.invoke, initial_state, config=thread_config)
    except Exception as exc:
        metrics.record_request((time.perf_counter() - start) * 1000, error=True)
        logger.error(json.dumps({"event": "error", "thread_id": thread_id, "error": str(exc)}))
        raise HTTPException(status_code=500, detail="Failed to generate report. Check server logs.")

    latency_ms = (time.perf_counter() - start) * 1000
    metrics.record_request(latency_ms)
    logger.info(json.dumps({
        "event": "research_started", "thread_id": thread_id, "topic": body.topic, "latency_ms": round(latency_ms),
    }))
    return _state_to_response(thread_id, result, interrupted="__interrupt__" in result)


@app.get("/research/{thread_id}", response_model=ResearchResponse, dependencies=[Depends(require_api_key)])
async def get_research(request: Request, thread_id: str) -> ResearchResponse:

    thread_config = {"configurable": {"thread_id": thread_id}}
    snapshot = await asyncio.to_thread(request.app.state.graph.get_state, thread_config)
    if not snapshot.values:
        raise HTTPException(status_code=404, detail=f"No research thread found for id {thread_id}")

    interrupted = bool(snapshot.next)  # non-empty means paused (awaiting review)
    return _state_to_response(thread_id, snapshot.values, interrupted)


@app.post("/research/{thread_id}/review", response_model=ResearchResponse, dependencies=[Depends(require_api_key)])
@limiter.limit(config.RATE_LIMIT)
async def review_research(request: Request, thread_id: str, body: ReviewRequest) -> ResearchResponse:

    thread_config = {"configurable": {"thread_id": thread_id}}
    snapshot = await asyncio.to_thread(request.app.state.graph.get_state, thread_config)
    if not snapshot.values:
        raise HTTPException(status_code=404, detail=f"No research thread found for id {thread_id}")
    if not snapshot.next:
        raise HTTPException(status_code=400, detail="This report is not awaiting review (already finalized, or not yet started).")

    if not body.approved:
        metrics.record_revision_requested()

    start = time.perf_counter()
    resume_payload = {"approved": body.approved, "feedback": body.feedback}
    try:
        result = await asyncio.to_thread(request.app.state.graph.invoke, Command(resume=resume_payload), config=thread_config)
    except Exception as exc:
        metrics.record_request((time.perf_counter() - start) * 1000, error=True)
        logger.error(json.dumps({"event": "error", "thread_id": thread_id, "error": str(exc)}))
        raise HTTPException(status_code=500, detail="Failed to process review. Check server logs.")

    latency_ms = (time.perf_counter() - start) * 1000
    metrics.record_request(latency_ms)
    if result.get("status") == "finalized":
        metrics.record_report_finalized()

    logger.info(json.dumps({
        "event": "review_processed", "thread_id": thread_id, "approved": body.approved,
        "revision_count": result.get("revision_count"), "latency_ms": round(latency_ms),
    }))
    return _state_to_response(thread_id, result, interrupted="__interrupt__" in result)
