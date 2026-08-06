"""
Centralized configuration. Every other module reads settings from here.

Reuses the same patterns as the rag-capstone project deliberately (provider
abstraction, Secret Manager fallback, structured tracing config) -- proof
that these patterns generalize across projects, not one-off tricks.
"""
import logging
import os

from dotenv import load_dotenv

load_dotenv()

_logger = logging.getLogger("config")


def _get_secret(env_name: str, default: str = "") -> str:
    """Env var first (local dev); falls back to GCP Secret Manager if
    GCP_PROJECT_ID is set. A lookup failure is logged, not silently
    swallowed -- an earlier version of this same function in the
    rag-capstone project used a bare `except: pass` here, which could mask
    a real misconfiguration as an empty secret. Fixed here from the start."""
    value = os.getenv(env_name)
    if value:
        return value

    project_id = os.getenv("GCP_PROJECT_ID")
    if project_id:
        try:
            from google.cloud import secretmanager
            client = secretmanager.SecretManagerServiceClient()
            secret_id = env_name.lower().replace("_", "-")
            name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
            response = client.access_secret_version(name=name)
            return response.payload.data.decode("UTF-8")
        except Exception as exc:
            _logger.warning(f"Secret Manager lookup for '{env_name}' failed, falling back to default: {exc}")
    return default


# --- Model provider (same pattern as rag-capstone) --------------------------
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "groq").lower()

GROQ_API_KEY = _get_secret("GROQ_API_KEY")
GROQ_CHAT_MODEL = os.getenv("GROQ_CHAT_MODEL", "llama-3.3-70b-versatile")

GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "")
GCP_LOCATION = os.getenv("GCP_LOCATION", "us-central1")
VERTEX_CHAT_MODEL = os.getenv("VERTEX_CHAT_MODEL", "gemini-2.0-flash-001")

LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
LLM_REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT", "60"))

# --- Per-agent model overrides (feature-flag style) --------------------------
# Each agent can be pinned to a specific model independent of the global
# default -- e.g. use a cheaper/faster model for the researcher's many
# search-summarization calls, and a stronger model for the writer's final
# prose. Empty string means "use the provider's default chat model."
RESEARCHER_MODEL_OVERRIDE = os.getenv("RESEARCHER_MODEL_OVERRIDE", "")
ANALYST_MODEL_OVERRIDE = os.getenv("ANALYST_MODEL_OVERRIDE", "")
WRITER_MODEL_OVERRIDE = os.getenv("WRITER_MODEL_OVERRIDE", "")

# --- Web search (Tavily) -----------------------------------------------------
TAVILY_API_KEY = _get_secret("TAVILY_API_KEY")
MAX_SEARCH_RESULTS = int(os.getenv("MAX_SEARCH_RESULTS", "5"))
SEARCH_DEPTH = os.getenv("SEARCH_DEPTH", "basic")  # "basic" or "advanced"
MAX_RESEARCH_QUERIES = int(os.getenv("MAX_RESEARCH_QUERIES", "4"))  # sub-queries per report

# --- API auth / rate limiting / CORS (same pattern as rag-capstone) ---------
API_KEY = _get_secret("API_KEY")
RATE_LIMIT = os.getenv("RATE_LIMIT", "10/minute")
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")

# --- LangSmith tracing --------------------------------------------------------
LANGSMITH_TRACING = os.getenv("LANGSMITH_TRACING", "false").lower() == "true"
if LANGSMITH_TRACING:
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_PROJECT"] = os.getenv("LANGSMITH_PROJECT", "multi-agent-research")

# --- Human-in-the-loop -------------------------------------------------------
# How long a paused (awaiting-review) thread is kept before being treated as
# abandoned -- purely informational for now (surfaced via /research/{id}
# status), not yet enforced by an eviction job. See README roadmap.
REVIEW_TIMEOUT_MINUTES = int(os.getenv("REVIEW_TIMEOUT_MINUTES", "60"))

# --- Database / Persistence --------------------------------------------------
DB_PATH = os.getenv("DB_PATH", "checkpoints.sqlite")

# --- Validation ----------------------------------------------------------------
if MODEL_PROVIDER == "groq" and not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY is not set. Copy .env.example to .env and add your key, "
        "or set MODEL_PROVIDER=vertexai to use Vertex AI instead."
    )
if MODEL_PROVIDER == "vertexai" and not GCP_PROJECT_ID:
    raise RuntimeError("MODEL_PROVIDER=vertexai requires GCP_PROJECT_ID to be set in .env.")
if not TAVILY_API_KEY:
    raise RuntimeError(
        "TAVILY_API_KEY is not set. Get a free key at https://app.tavily.com "
        "(no credit card required) and add it to .env."
    )
