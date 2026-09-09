from __future__ import annotations

from unittest.mock import Mock

import pytest

from mind.ai import ConfigurationError, Opus5Gateway
from mind.brain import check_line, run_turn, validate_claim


def test_validate_claim_refuses_without_falsifier():
    with pytest.raises(ValueError, match="falsifier"):
        validate_claim({"text": "This is a claim.", "falsifier": ""})


def test_check_line_blocks_banned_phrasings():
    assert check_line("This is guaranteed to be true.") is True
    assert check_line("The review is obviously biased.") is True
    assert check_line("A careful reading suggests a likely pattern.") is False


def test_run_turn_returns_offline_useful_result_when_catalogs_fail():
    result = run_turn(
        "What is the production status of the work?",
        claims=[],
        catalogs={
            "steam": lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
            "tmdb": lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
            "anilist": lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
            "musicbrainz": lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
        },
    )

    assert result["offline"] is True
    assert "offline" in result["answer"].lower()
    assert len(result["answer"]) > 20


def test_gateway_generate_uses_anthropic_sdk_and_parses_text(monkeypatch):
    gateway = Opus5Gateway(api_key="test-key")

    response = Mock()
    response.content = [Mock(type="text", text="The model answered with reasoning.")]

    client = Mock()
    client.messages.create.return_value = response
    monkeypatch.setattr("mind.ai.Anthropic", lambda **kwargs: client)

    assert gateway.generate("Summarize the claim.") == "The model answered with reasoning."
    client.messages.create.assert_called_once()


def test_gateway_generate_structured_rejects_invalid_json(monkeypatch):
    gateway = Opus5Gateway(api_key="test-key")

    response = Mock()
    response.content = [Mock(type="text", text="not-json")]

    client = Mock()
    client.messages.create.return_value = response
    monkeypatch.setattr("mind.ai.Anthropic", lambda **kwargs: client)

    with pytest.raises(ValueError, match="valid JSON"):
        gateway.generate_structured("Return JSON", {"status": "string"})


def test_gateway_requires_api_key_at_call_time(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    gateway = Opus5Gateway(api_key=None)

    with pytest.raises(ConfigurationError, match="API key"):
        gateway.generate("This should fail.")


def test_gateway_passes_workspace_header_when_configured(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "workspace-123")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    gateway = Opus5Gateway(api_key="test-key")

    response = Mock()
    response.content = [Mock(type="text", text="ok")]

    client = Mock()
    client.messages.create.return_value = response
    monkeypatch.setattr("mind.ai.Anthropic", lambda **kwargs: client)

    assert gateway.generate("Hello") == "ok"
    assert client.messages.create.call_args.kwargs["extra_headers"] == {"anthropic-workspace-id": "workspace-123"}
