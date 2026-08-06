"""
Model provider factory -- same get_llm() pattern as rag-capstone, extended
with an optional `agent_override` parameter so each agent (researcher,
analyst, writer) can be pinned to a different model via config, without
touching agent code. This is the "feature flags for model updates" pattern:
swapping a model for one agent is a .env change, not a redeploy.
"""
from functools import lru_cache

from app import config


@lru_cache(maxsize=16)
def get_llm(temperature: float = 0.0, model_override: str = ""):
    """
    model_override: an explicit model name to use instead of the provider's
    default (e.g. config.RESEARCHER_MODEL_OVERRIDE). Empty string means
    "use the provider's configured default chat model."
    """
    if config.MODEL_PROVIDER == "vertexai":
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
