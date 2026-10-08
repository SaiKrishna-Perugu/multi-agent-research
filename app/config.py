"""
Centralized configuration. Every other module reads settings from here,
never `os.getenv` directly -- keeps provider abstraction, Secret Manager
fallback, and validation timing consistent in one place.
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

_logger = logging.getLogger("config")


def _get_secret(env_name: str, default: str = "") -> str:
    """Env var first (local dev); falls back to GCP Secret Manager if
    GCP_PROJECT_ID is set."""
    if env_name in os.environ:
        return os.environ[env_name]

    project_id = os.getenv("GCP_PROJECT_ID")
    if project_id:
        if (
            env_name == "API_KEY"
            and os.getenv("AUTH_USE_SECRET_MANAGER", "false").lower() != "true"
        ):
            return default
        try:
            from google.cloud import secretmanager

            client = secretmanager.SecretManagerServiceClient()
            secret_id = env_name.lower().replace("_", "-")
            name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
            response = client.access_secret_version(name=name)
            return response.payload.data.decode("UTF-8")
        except Exception as exc:
            # Logged, not swallowed -- a bare `except: pass` here would mask
            # a real misconfiguration as an empty secret.
            _logger.warning(
                f"Secret Manager lookup for '{env_name}' failed, falling back to default: {exc}"
            )
    return default


# --- Model provider -----------------------------------------------------
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "groq").lower()

GROQ_API_KEY = _get_secret("GROQ_API_KEY")
GROQ_CHAT_MODEL = os.getenv("GROQ_CHAT_MODEL", "openai/gpt-oss-120b")

GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "")
GCP_LOCATION = os.getenv("GCP_LOCATION", "global")
VERTEX_CHAT_MODEL = os.getenv("VERTEX_CHAT_MODEL", "gemini-3.5-flash")

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
MAX_RESEARCH_QUERIES = int(
    os.getenv("MAX_RESEARCH_QUERIES", "4")
)  # sub-queries per report

# --- API auth / rate limiting / CORS / Concurrency -----------------------
API_KEY = _get_secret("API_KEY")
RATE_LIMIT = os.getenv("RATE_LIMIT", "10/minute")
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "*").split(",")
    if origin.strip()
]
MAX_ACTIVE_RUNS = int(os.getenv("MAX_ACTIVE_RUNS", "1"))

# --- LangSmith tracing --------------------------------------------------------
LANGSMITH_TRACING = os.getenv("LANGSMITH_TRACING", "false").lower() == "true"
if LANGSMITH_TRACING:
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_PROJECT"] = os.getenv(
        "LANGSMITH_PROJECT", "multi-agent-research"
    )

# --- Human-in-the-loop -------------------------------------------------------
# Review window checked when polling or submitting feedback. Expired reviews
# are rejected; a background eviction job does not delete old checkpoints.
REVIEW_TIMEOUT_MINUTES = int(os.getenv("REVIEW_TIMEOUT_MINUTES", "60"))

# --- Database / Persistence --------------------------------------------------
DB_PATH = os.getenv("DB_PATH", "checkpoints.sqlite")

# --- TypeSafe System One (intelligent judgment) ------------------------------
TYPESAFE_API_KEY = _get_secret("TYPESAFE_API_KEY")
TYPESAFE_MODEL = os.getenv("TYPESAFE_MODEL", "jev-1.13.0")
ENABLE_TYPESAFE = os.getenv("ENABLE_TYPESAFE", "true").lower() in ("true", "1")


def is_typesafe_available() -> bool:
    return bool(TYPESAFE_API_KEY and ENABLE_TYPESAFE)


def validate_llm_config() -> None:
    if MODEL_PROVIDER not in {"groq", "vertexai"}:
        raise RuntimeError("MODEL_PROVIDER must be 'groq' or 'vertexai'.")
    if LLM_REQUEST_TIMEOUT <= 0:
        raise RuntimeError("LLM_REQUEST_TIMEOUT must be greater than zero seconds.")
    if LLM_MAX_RETRIES < 0:
        raise RuntimeError("LLM_MAX_RETRIES must be zero or greater.")
    if MODEL_PROVIDER == "groq" and not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Copy .env.example to .env and add your key, "
            "or set MODEL_PROVIDER=vertexai to use Vertex AI instead."
        )
    if MODEL_PROVIDER == "vertexai" and not GCP_PROJECT_ID:
        raise RuntimeError(
            "MODEL_PROVIDER=vertexai requires GCP_PROJECT_ID to be set in .env."
        )


def validate_search_config() -> None:
    if not TAVILY_API_KEY:
        raise RuntimeError(
            "TAVILY_API_KEY is not set. Get a free key at https://app.tavily.com "
            "(no credit card required) and add it to .env."
        )
