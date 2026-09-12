from __future__ import annotations

import os
from unittest.mock import Mock

import pytest

from mind.ai import SAMPLING_PARAMETERS, ConfigurationError, Opus5Gateway
from mind.brain import check_line, run_turn, validate_claim


def test_validate_claim_refuses_without_falsifier():
    with pytest.raises(ValueError, match="falsifier"):
        validate_claim({"text": "This is a claim.", "falsifier": ""})


def test_check_line_blocks_banned_phrasings():
    assert check_line("This is guaranteed to be true.") is True
    assert check_line("The review is obviously biased.") is True
    assert check_line("A careful reading suggests a likely pattern.") is False


def test_run_turn_returns_offline_useful_result_when_catalogs_fail(monkeypatch):
    # Offline behaviour must be tested offline. Without this the result depends on
    # whether a real key happens to be present in .env on the machine running the suite.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
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


def test_gateway_never_sends_a_sampling_parameter(monkeypatch):
    # Opus 5 rejects temperature/top_p/top_k with a 400, which is not retryable, so one
    # of these in the payload fails every request and surfaces as "model unavailable".
    captured: dict[str, object] = {}

    response = Mock()
    response.content = [Mock(type="text", text="answered")]
    client = Mock()
    client.messages.create.side_effect = lambda **kwargs: captured.update(kwargs) or response
    monkeypatch.setattr("mind.ai.Anthropic", lambda **kwargs: client)

    gateway = Opus5Gateway(api_key="test-key")
    gateway.generate("Summarize the claim.", system="be brief")
    assert not SAMPLING_PARAMETERS & set(captured)

    captured.clear()
    gateway.converse([{"role": "user", "content": "hello"}])
    assert not SAMPLING_PARAMETERS & set(captured)

    captured.clear()
    response.content = [Mock(type="text", text='{"status": "ok"}')]
    gateway.generate_structured("Return JSON", {"status": "string"})
    assert not SAMPLING_PARAMETERS & set(captured)


def test_gateway_converse_sends_the_whole_conversation(monkeypatch):
    captured: dict[str, object] = {}
    response = Mock()
    response.content = [Mock(type="text", text="answered")]
    client = Mock()
    client.messages.create.side_effect = lambda **kwargs: captured.update(kwargs) or response
    monkeypatch.setattr("mind.ai.Anthropic", lambda **kwargs: client)

    Opus5Gateway(api_key="test-key").converse(
        [
            # A recalled conversation can open on something Kit said; the API cannot.
            {"role": "assistant", "content": "dropped, the API needs a user turn first"},
            {"role": "user", "content": "earlier question"},
            {"role": "assistant", "content": "earlier answer"},
            {"role": "user", "content": "follow-up"},
        ],
        system="be brief",
    )

    assert captured["messages"] == [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
        {"role": "user", "content": "follow-up"},
    ]
    assert captured["system"] == "be brief"


def test_gateway_converse_requires_a_user_turn(monkeypatch):
    monkeypatch.setattr("mind.ai.Anthropic", lambda **kwargs: Mock())

    with pytest.raises(ValueError, match="user message"):
        Opus5Gateway(api_key="test-key").converse([{"role": "assistant", "content": "only me"}])


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


def test_gateway_loads_keys_from_dotenv_file(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=test-key\nANTHROPIC_WORKSPACE_ID=workspace-123\n", encoding="utf-8")

    import importlib
    import mind.ai as mind_ai

    try:
        importlib.reload(mind_ai)
        gateway = mind_ai.Opus5Gateway(api_key=None)

        assert gateway.api_key == "test-key"
        assert os.getenv("ANTHROPIC_WORKSPACE_ID") == "workspace-123"
    finally:
        # _load_dotenv_file writes into os.environ at import time. monkeypatch cannot
        # undo that, because monkeypatch did not make the change, so without this the
        # key leaks into every test that runs afterwards.
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("ANTHROPIC_WORKSPACE_ID", None)


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


def test_generate_structured_makes_one_request_and_sends_the_schema(monkeypatch):
    gateway = Opus5Gateway(api_key="test-key")

    response = Mock()
    response.content = [Mock(type="text", text='{"final_answer": "done"}')]

    client = Mock()
    client.messages.create.return_value = response
    monkeypatch.setattr("mind.ai.Anthropic", lambda **kwargs: client)

    schema = {"type": "object", "properties": {"final_answer": {"type": "string"}}}
    assert gateway.generate_structured("Answer the question", schema) == {"final_answer": "done"}

    client.messages.create.assert_called_once()
    kwargs = client.messages.create.call_args.kwargs
    assert "final_answer" in kwargs["messages"][0]["content"]
    assert kwargs["system"].startswith("Respond with valid JSON")


def test_generate_structured_tolerates_a_fenced_json_block(monkeypatch):
    gateway = Opus5Gateway(api_key="test-key")

    response = Mock()
    response.content = [Mock(type="text", text='```json\n{"final_answer": "fenced"}\n```')]

    client = Mock()
    client.messages.create.return_value = response
    monkeypatch.setattr("mind.ai.Anthropic", lambda **kwargs: client)

    assert gateway.generate_structured("Answer", {"type": "object"}) == {"final_answer": "fenced"}


def test_request_retries_on_status_code_and_gives_up_on_client_error(monkeypatch):
    monkeypatch.setattr("mind.ai.time.sleep", lambda _seconds: None)

    class Transient(Exception):
        status_code = 503

    class Forbidden(Exception):
        status_code = 403

    ok = Mock()
    ok.content = [Mock(type="text", text="recovered")]

    gateway = Opus5Gateway(api_key="test-key")
    client = Mock()
    client.messages.create.side_effect = [Transient(), Transient(), ok]
    monkeypatch.setattr("mind.ai.Anthropic", lambda **kwargs: client)
    assert gateway.generate("Hello") == "recovered"
    assert client.messages.create.call_count == 3

    refusing = Mock()
    refusing.messages.create.side_effect = Forbidden()
    monkeypatch.setattr("mind.ai.Anthropic", lambda **kwargs: refusing)
    with pytest.raises(Forbidden):
        Opus5Gateway(api_key="test-key").generate("Hello")
    assert refusing.messages.create.call_count == 1


def test_run_turn_feeds_tool_results_back_and_never_repeats_a_call(monkeypatch):
    calls: list[dict] = []

    def flaky_tool(_question, _claims, **kwargs):
        calls.append(kwargs)
        return {"tool": "steam", "source": "steam", "ok": False, "fetched_at": None, "error": "offline"}

    seen_payloads: list[str] = []

    def fake_structured(self, prompt, _schema, **_kwargs):
        seen_payloads.append(prompt)
        return {"tool_calls": [{"name": "steam", "arguments": {"q": "same"}}]}

    monkeypatch.setattr(Opus5Gateway, "generate_structured", fake_structured)

    result = run_turn("Is it out yet?", claims=[], catalogs={"steam": flaky_tool})

    assert len(calls) == 1
    assert result["offline"] is True
    assert "tool_results" in seen_payloads[-1]


def test_run_turn_survives_a_tool_that_returns_the_wrong_shape(monkeypatch):
    def bad_tool(_question, _claims, **_kwargs):
        return "not a mapping"

    def fake_structured(self, _prompt, _schema, **_kwargs):
        return {"tool_calls": [{"name": "steam", "arguments": {}}, "not-a-dict"]}

    monkeypatch.setattr(Opus5Gateway, "generate_structured", fake_structured)

    result = run_turn("Is it out yet?", claims=[], catalogs={"steam": bad_tool})

    assert result["offline"] is True
    assert result["tool_results"][0]["ok"] is False
    assert "expected a mapping" in result["tool_results"][0]["error"]


def test_run_turn_grounds_the_offline_answer_in_the_vault_map(tmp_path, monkeypatch):
    from mind.embeddings import StubEmbedder
    from mind.obsidian_brain import ObsidianBrain

    brain = ObsidianBrain(vault_root=tmp_path / "vault", embedder=StubEmbedder())
    brain.ingest("Solaris", "A slow film about grief.", layer="elicited", source="user")
    brain.ingest("Grief on film", "Compare [[Solaris]] with other slow films.", layer="inferred", source="kit")

    def unavailable(self, *_args, **_kwargs):
        raise RuntimeError("no api key")

    monkeypatch.setattr(Opus5Gateway, "generate_structured", unavailable)

    result = run_turn("something slow about grief", claims=[], catalogs={}, brain=brain)

    assert result["offline"] is True
    titles = {note["title"] for note in result["vault_evidence"]}
    assert titles
    # The answer names what it rests on and carries the layer, rather than claiming
    # there is nothing when the vault in fact holds relevant notes.
    assert "vault record" in result["answer"]
    assert any(layer in result["answer"] for layer in ("elicited", "inferred"))


def test_run_turn_without_a_brain_still_reports_an_honest_empty(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = run_turn("anything", claims=[], catalogs={})

    assert result["vault_evidence"] == []
    assert "offline" in result["answer"].lower()
