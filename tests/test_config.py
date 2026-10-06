"""Reject configuration that would defeat provider selection or deadlines."""

import pytest

from app import config


@pytest.fixture
def valid_llm_config(monkeypatch):
    monkeypatch.setattr(config, "MODEL_PROVIDER", "groq")
    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(config, "GCP_PROJECT_ID", "test-project")
    monkeypatch.setattr(config, "LLM_REQUEST_TIMEOUT", 7)
    monkeypatch.setattr(config, "LLM_MAX_RETRIES", 0)


@pytest.mark.parametrize("provider", ["groq", "vertexai"])
def test_valid_provider_with_zero_retries(valid_llm_config, monkeypatch, provider):
    monkeypatch.setattr(config, "MODEL_PROVIDER", provider)
    config.validate_llm_config()


@pytest.mark.parametrize("provider", ["vertex", "grog", ""])
def test_unknown_provider_is_rejected(valid_llm_config, monkeypatch, provider):
    monkeypatch.setattr(config, "MODEL_PROVIDER", provider)
    with pytest.raises(RuntimeError, match="MODEL_PROVIDER must be"):
        config.validate_llm_config()


@pytest.mark.parametrize("timeout", [0, -1])
def test_nonpositive_timeout_is_rejected(valid_llm_config, monkeypatch, timeout):
    monkeypatch.setattr(config, "LLM_REQUEST_TIMEOUT", timeout)
    with pytest.raises(RuntimeError, match="LLM_REQUEST_TIMEOUT must be"):
        config.validate_llm_config()


def test_negative_retry_count_is_rejected(valid_llm_config, monkeypatch):
    monkeypatch.setattr(config, "LLM_MAX_RETRIES", -1)
    with pytest.raises(RuntimeError, match="LLM_MAX_RETRIES must be"):
        config.validate_llm_config()
