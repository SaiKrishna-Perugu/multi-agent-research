"""Tests for app/typesafe_client.py System One evaluation and classifications."""

from unittest.mock import MagicMock, patch

from app import config
from app.typesafe_client import (
    classify_review_intent,
    classify_runtime_error,
    evaluate_system_one,
    get_typesafe_client,
)


def test_get_typesafe_client_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "TYPESAFE_API_KEY", "")
    assert get_typesafe_client() is None


def test_get_typesafe_client_initializes_when_configured(monkeypatch):
    monkeypatch.setattr(config, "TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(config, "ENABLE_TYPESAFE", True)

    with patch("typesafe_sdk.TypeSafeClient") as mock_cls:
        import app.typesafe_client

        app.typesafe_client._client = None
        client = get_typesafe_client()
        assert client is not None
        mock_cls.assert_called_once_with(api_key="test-key", timeout=30.0)


def test_evaluate_system_one_fails_open_on_error(monkeypatch):
    monkeypatch.setattr(config, "TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(config, "ENABLE_TYPESAFE", True)

    mock_client = MagicMock()
    mock_client.system_one.side_effect = RuntimeError("network connection drop")

    with patch("app.typesafe_client.get_typesafe_client", return_value=mock_client):
        res = evaluate_system_one("state", {})
        assert res is None


def test_classify_review_intent_identifies_research_gap(monkeypatch):
    monkeypatch.setattr(config, "TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(config, "ENABLE_TYPESAFE", True)

    mock_choice = MagicMock()
    mock_choice.choice = "research_gap"
    mock_res = MagicMock()
    mock_res.choices = {"intent": mock_choice}

    with patch("app.typesafe_client.evaluate_system_one", return_value=mock_res):
        intent = classify_review_intent(
            "We are completely missing data on UK nuclear reactor deployments in 2025"
        )
        assert intent == "research_gap"


def test_classify_review_intent_identifies_textual_revision(monkeypatch):
    monkeypatch.setattr(config, "TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(config, "ENABLE_TYPESAFE", True)

    mock_choice = MagicMock()
    mock_choice.choice = "revise"
    mock_res = MagicMock()
    mock_res.choices = {"intent": mock_choice}

    with patch("app.typesafe_client.evaluate_system_one", return_value=mock_res):
        intent = classify_review_intent(
            "Tone is too informal, please rewrite the conclusion"
        )
        assert intent == "revise"


def test_classify_review_intent_falls_back_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "TYPESAFE_API_KEY", "")
    assert classify_review_intent("Any feedback text without TypeSafe key") == "revise"


def test_classify_runtime_error_identifies_rate_limit(monkeypatch):
    monkeypatch.setattr(config, "TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(config, "ENABLE_TYPESAFE", True)

    mock_choice = MagicMock()
    mock_choice.choice = "rate_limit"
    mock_res = MagicMock()
    mock_res.choices = {"error_category": mock_choice}

    with patch("app.typesafe_client.evaluate_system_one", return_value=mock_res):
        category = classify_runtime_error(RuntimeError("HTTP 429: TPM limit exceeded"))
        assert category == "rate_limit"


def test_classify_runtime_error_falls_back_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "TYPESAFE_API_KEY", "")
    assert classify_runtime_error(RuntimeError("some crash")) == "system_error"


def test_classify_runtime_error_handles_exception_gracefully(monkeypatch):
    monkeypatch.setattr(config, "TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(config, "ENABLE_TYPESAFE", True)

    with patch(
        "app.typesafe_client.evaluate_system_one",
        side_effect=RuntimeError("internal API error"),
    ):
        category = classify_runtime_error(RuntimeError("some crash"))
        assert category == "system_error"


def test_classify_review_intent_handles_exception_gracefully(monkeypatch):
    monkeypatch.setattr(config, "TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(config, "ENABLE_TYPESAFE", True)

    with patch(
        "app.typesafe_client.evaluate_system_one",
        side_effect=RuntimeError("internal API error"),
    ):
        intent = classify_review_intent("some feedback")
        assert intent == "revise"


def test_get_typesafe_client_catches_init_exception(monkeypatch):
    monkeypatch.setattr(config, "TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(config, "ENABLE_TYPESAFE", True)

    with patch("typesafe_sdk.TypeSafeClient", side_effect=Exception("init error")):
        import app.typesafe_client

        app.typesafe_client._client = None
        assert get_typesafe_client() is None


def test_human_review_node_infers_intent_via_typesafe(monkeypatch):
    from unittest.mock import patch

    from app.graph import human_review_node

    monkeypatch.setattr(config, "TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(config, "ENABLE_TYPESAFE", True)

    with patch(
        "app.graph.interrupt",
        return_value={"approved": False, "feedback": "missing key data", "action": ""},
    ):
        with patch(
            "app.typesafe_client.classify_review_intent", return_value="research_gap"
        ):
            res = human_review_node(
                {"draft": "draft v1", "revision_count": 0, "status": "drafted"}
            )
            assert res["review_action"] == "research_gap"
            assert res["status"] == "revision_requested"
            assert res["revision_count"] == 1
