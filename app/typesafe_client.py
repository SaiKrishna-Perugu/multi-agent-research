"""
TypeSafe System One client wrapper.

Provides fast, structured semantic judgments (Choice, Score, Noul) with
calibrated probabilities and confidence metrics. Completely fail-open:
if TYPESAFE_API_KEY is not configured or an evaluation fails, all methods
return None or graceful fallbacks so the application degrades safely.
"""

import logging
from typing import Any

from app import config

logger = logging.getLogger("typesafe")

_client = None


def get_typesafe_client():
    """Return the singleton TypeSafeClient if configured, else None."""
    global _client
    if not config.is_typesafe_available():
        return None
    if _client is None:
        try:
            from typesafe_sdk import TypeSafeClient

            _client = TypeSafeClient(
                api_key=config.TYPESAFE_API_KEY,
                timeout=30.0,
            )
        except Exception as exc:
            logger.warning("Failed to initialize TypeSafeClient: %s", exc)
            return None
    return _client


def evaluate_system_one(
    state: Any,
    questions: dict,
    model: str | None = None,
) -> Any:
    """Execute a System One evaluation against state with named questions.

    Returns SystemOneResponse on success, or None on failure/unconfigured.
    """
    client = get_typesafe_client()
    if client is None:
        return None

    use_model = model or config.TYPESAFE_MODEL
    try:
        return client.system_one(
            state=state,
            questions=questions,
            model=use_model,
        )
    except Exception as exc:
        logger.warning("TypeSafe evaluation error (falling back to default): %s", exc)
        return None


def classify_review_intent(feedback: str, draft_context: str = "") -> str:
    """Classify reviewer feedback into an action: 'research_gap' or 'revise'.

    If the reviewer indicates missing facts, questions, or external sources needed,
    returns 'research_gap' to route to researcher. Otherwise returns 'revise'.
    Falls back to 'revise' if TypeSafe is unavailable.
    """
    if not config.is_typesafe_available() or not feedback.strip():
        return "revise"

    try:
        from typesafe_sdk import Choice

        state = {
            "feedback": feedback,
            "draft_excerpt": draft_context[:600] if draft_context else "",
        }
        questions = {
            "intent": Choice(
                instructions=(
                    "Classify this reviewer feedback on a research draft report into the appropriate pipeline action."
                ),
                criteria={
                    "revise": "Feedback asks for rewording, reorganization, styling, tone adjustments, or shortening from existing findings",
                    "research_gap": "Feedback points out missing facts, unaddressed questions, new topics, or requests additional data/sources requiring fresh web searches",
                },
            )
        }
        res = evaluate_system_one(state, questions)
        if res and hasattr(res, "choices") and "intent" in res.choices:
            choice = res.choices["intent"].choice
            if choice in ("revise", "research_gap"):
                return choice
    except Exception as exc:
        logger.warning(
            "TypeSafe review intent classification error, falling back: %s", exc
        )

    return "revise"


def classify_runtime_error(exc: Exception) -> str:
    """Classify an unhandled exception into a failure category using TypeSafe Choice.

    Returns one of: 'rate_limit', 'context_length', 'network_timeout', 'auth_error', 'system_error'.
    Falls back to 'system_error' if TypeSafe is unavailable.
    """
    if not config.is_typesafe_available():
        return "system_error"

    try:
        from typesafe_sdk import Choice

        state = {
            "exception_type": type(exc).__name__,
            "message": str(exc),
        }
        questions = {
            "error_category": Choice(
                instructions="Classify this runtime error message into the appropriate category.",
                criteria={
                    "rate_limit": "Rate limiting, TPM/RPM quota exceeded, 429 Too Many Requests, or provider throttling",
                    "context_length": "Context window exceeded, token limit, 413 Payload Too Large, or prompt too long",
                    "network_timeout": "HTTP timeout, connection reset, 502/503/504 gateway error, or unreachable host",
                    "auth_error": "Authentication failure, missing or invalid API key, 401 Unauthorized, or 403 Forbidden",
                    "system_error": "Syntax error, internal logic bug, missing attribute, or other unexpected crash",
                },
            )
        }
        res = evaluate_system_one(state, questions)
        if res and hasattr(res, "choices") and "error_category" in res.choices:
            choice = res.choices["error_category"].choice
            if choice:
                return choice
    except Exception as exc_inner:
        logger.warning(
            "TypeSafe error classification error, falling back: %s", exc_inner
        )

    return "system_error"
