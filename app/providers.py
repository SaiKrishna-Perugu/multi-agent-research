"""
Model provider factory. get_llm() takes an optional `model_override`
parameter so each agent (researcher, analyst, writer) can be pinned to a
different model via config, without touching agent code. This is the
"feature flags for model updates" pattern: swapping a model for one agent
is a .env change, not a redeploy.
"""
from functools import lru_cache

from app import config


def get_llm(temperature: float = 0.0, model_override: str = ""):
    """
    model_override: an explicit model name to use instead of the provider's
    default (e.g. config.RESEARCHER_MODEL_OVERRIDE). Empty string means
    "use the provider's configured default chat model."
    """
    # config.MODEL_PROVIDER is threaded through explicitly so it's part of the
    # cache key -- without it, a provider change (e.g. in tests, or a future
    # hot-reload) with the same (temperature, model_override) would return a
    # stale LLM instance built for the previous provider.
    return _build_llm(config.MODEL_PROVIDER, temperature, model_override)


@lru_cache(maxsize=16)
def _build_llm(provider: str, temperature: float, model_override: str):
    config.validate_llm_config()

    if provider == "vertexai":
        from langchain_google_vertexai import ChatVertexAI
        return ChatVertexAI(
            model_name=model_override or config.VERTEX_CHAT_MODEL,
            project=config.GCP_PROJECT_ID,
            location=config.GCP_LOCATION,
            temperature=temperature,
            max_retries=config.LLM_MAX_RETRIES,
        )

    from langchain_groq import ChatGroq
    return ChatGroq(
        model=model_override or config.GROQ_CHAT_MODEL,
        temperature=temperature,
        max_retries=config.LLM_MAX_RETRIES,
        timeout=config.LLM_REQUEST_TIMEOUT,
    )
