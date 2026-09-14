from __future__ import annotations

import json
import os

import pytest

from mind.ai import SAMPLING_PARAMETERS, ConfigurationError, GeminiAPIError, GeminiGateway
from mind.brain import check_line, run_turn, validate_claim


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


def _reply(*parts: dict) -> FakeResponse:
    return FakeResponse({"candidates": [{"content": {"role": "model", "parts": list(parts)}}]})


def _text(text: str) -> FakeResponse:
    return _reply({"text": text})


def _fake_http(monkeypatch, *responses: FakeResponse) -> list[dict]:
    """Stand in for the HTTP call. The last response repeats once the others are used."""
    calls: list[dict] = []
    queue = list(responses)

    def fake_post(url, *, json, headers, timeout):
        calls.append({"url": url, "body": json, "headers": headers})
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr("mind.ai._http_post", fake_post)
    return calls


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
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
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


def test_gateway_generate_parses_text_and_leaves_out_thoughts(monkeypatch):
    calls = _fake_http(
        monkeypatch,
        _reply({"text": "weighing the claim", "thought": True}, {"text": "The model answered with reasoning."}),
    )

    gateway = GeminiGateway(api_key="test-key", model="gemini-test")
    assert gateway.generate("Summarize the claim.") == "The model answered with reasoning."

    assert len(calls) == 1
    assert calls[0]["url"].endswith("/models/gemini-test:generateContent")
    assert calls[0]["headers"] == {"x-goog-api-key": "test-key"}
    # The key goes in the header only, never into a URL that could be logged.
    assert "test-key" not in calls[0]["url"]


def test_gateway_never_sends_a_sampling_parameter(monkeypatch):
    # Gemini 3 degrades silently when temperature is lowered, so no sampler may be sent.
    calls = _fake_http(monkeypatch, _text('{"status": "ok"}'))
    gateway = GeminiGateway(api_key="test-key")

    gateway.generate("Summarize the claim.", system="be brief")
    gateway.converse([{"role": "user", "content": "hello"}])
    gateway.generate_structured("Return JSON", {"status": "string"})

    assert len(calls) == 3
    for call in calls:
        assert not SAMPLING_PARAMETERS & set(call["body"])
        assert not SAMPLING_PARAMETERS & set(call["body"]["generationConfig"])


def test_gateway_converse_sends_the_whole_conversation(monkeypatch):
    calls = _fake_http(monkeypatch, _text("answered"))

    GeminiGateway(api_key="test-key").converse(
        [
            # A recalled conversation can open on something Kit said; the API cannot.
            {"role": "assistant", "content": "dropped, the API needs a user turn first"},
            {"role": "user", "content": "earlier question"},
            {"role": "assistant", "content": "earlier answer"},
            {"role": "user", "content": "follow-up"},
        ],
        system="be brief",
    )

    body = calls[0]["body"]
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "earlier question"}]},
        {"role": "model", "parts": [{"text": "earlier answer"}]},
        {"role": "user", "parts": [{"text": "follow-up"}]},
    ]
    assert body["systemInstruction"] == {"parts": [{"text": "be brief"}]}


def test_gateway_converse_requires_a_user_turn(monkeypatch):
    _fake_http(monkeypatch, _text("unused"))

    with pytest.raises(ValueError, match="user message"):
        GeminiGateway(api_key="test-key").converse([{"role": "assistant", "content": "only me"}])


def test_gateway_generate_structured_rejects_invalid_json(monkeypatch):
    _fake_http(monkeypatch, _text("not-json"))

    with pytest.raises(ValueError, match="valid JSON"):
        GeminiGateway(api_key="test-key").generate_structured("Return JSON", {"status": "string"})


def test_gateway_requires_api_key_at_call_time(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    gateway = GeminiGateway(api_key=None)

    with pytest.raises(ConfigurationError, match="API key"):
        gateway.generate("This should fail.")


def test_gateway_loads_key_and_model_from_dotenv_file(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    (tmp_path / ".env").write_text("GEMINI_API_KEY=test-key\nGEMINI_MODEL=gemini-from-env\n", encoding="utf-8")

    import mind.ai as mind_ai

    try:
        # Call the loader rather than reloading the module: a reload redefines
        # GeminiAPIError, and every test after it would catch the stale class.
        mind_ai._load_dotenv_file()
        gateway = mind_ai.GeminiGateway(api_key=None)

        assert gateway.api_key == "test-key"
        assert gateway.model == "gemini-from-env"
    finally:
        # _load_dotenv_file writes into os.environ directly. monkeypatch cannot undo
        # that, because monkeypatch did not make the change, so without this the key
        # leaks into every test that runs afterwards.
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("GEMINI_MODEL", None)


def test_gateway_maps_effort_to_thinking_level(monkeypatch):
    calls = _fake_http(monkeypatch, _text("ok"))
    gateway = GeminiGateway(api_key="test-key")

    gateway.generate("Hello", effort="low")
    gateway.generate("Hello")

    assert calls[0]["body"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    assert "thinkingConfig" not in calls[1]["body"]["generationConfig"]


def test_generate_structured_makes_one_request_and_sends_the_schema(monkeypatch):
    calls = _fake_http(monkeypatch, _text('{"final_answer": "done"}'))

    schema = {"type": "object", "properties": {"final_answer": {"type": "string"}}}
    assert GeminiGateway(api_key="test-key").generate_structured("Answer the question", schema) == {
        "final_answer": "done"
    }

    assert len(calls) == 1
    body = calls[0]["body"]
    assert "final_answer" in body["contents"][0]["parts"][0]["text"]
    assert body["systemInstruction"]["parts"][0]["text"].startswith("Respond with valid JSON")
    assert body["generationConfig"]["responseMimeType"] == "application/json"


def test_generate_structured_tolerates_a_fenced_json_block(monkeypatch):
    _fake_http(monkeypatch, _text('```json\n{"final_answer": "fenced"}\n```'))

    assert GeminiGateway(api_key="test-key").generate_structured("Answer", {"type": "object"}) == {
        "final_answer": "fenced"
    }


def test_request_retries_on_status_code_and_gives_up_on_client_error(monkeypatch):
    monkeypatch.setattr("mind.ai.time.sleep", lambda _seconds: None)
    unavailable = FakeResponse({"error": {"message": "overloaded"}}, status_code=503)

    calls = _fake_http(monkeypatch, unavailable, unavailable, _text("recovered"))
    assert GeminiGateway(api_key="test-key").generate("Hello") == "recovered"
    assert len(calls) == 3

    calls = _fake_http(monkeypatch, FakeResponse({"error": {"message": "forbidden"}}, status_code=403))
    with pytest.raises(GeminiAPIError) as raised:
        GeminiGateway(api_key="test-key").generate("Hello")
    assert raised.value.status_code == 403
    assert len(calls) == 1


def test_api_error_carries_the_message_but_not_the_key(monkeypatch):
    _fake_http(monkeypatch, FakeResponse({"error": {"message": "API key not valid"}}, status_code=400))

    with pytest.raises(GeminiAPIError) as raised:
        GeminiGateway(api_key="secret-key-value").generate("Hello")

    assert "API key not valid" in str(raised.value)
    assert "secret-key-value" not in str(raised.value)


def test_run_turn_feeds_tool_results_back_and_never_repeats_a_call(monkeypatch):
    calls: list[dict] = []

    def flaky_tool(_question, _claims, **kwargs):
        calls.append(kwargs)
        return {"tool": "steam", "source": "steam", "ok": False, "fetched_at": None, "error": "offline"}

    seen_payloads: list[str] = []

    def fake_structured(self, prompt, _schema, **_kwargs):
        seen_payloads.append(prompt)
        return {"tool_calls": [{"name": "steam", "arguments": {"q": "same"}}]}

    monkeypatch.setattr(GeminiGateway, "generate_structured", fake_structured)

    result = run_turn("Is it out yet?", claims=[], catalogs={"steam": flaky_tool})

    assert len(calls) == 1
    assert result["offline"] is True
    assert "tool_results" in seen_payloads[-1]


def test_run_turn_survives_a_tool_that_returns_the_wrong_shape(monkeypatch):
    def bad_tool(_question, _claims, **_kwargs):
        return "not a mapping"

    def fake_structured(self, _prompt, _schema, **_kwargs):
        return {"tool_calls": [{"name": "steam", "arguments": {}}, "not-a-dict"]}

    monkeypatch.setattr(GeminiGateway, "generate_structured", fake_structured)

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

    monkeypatch.setattr(GeminiGateway, "generate_structured", unavailable)

    result = run_turn("something slow about grief", claims=[], catalogs={}, brain=brain)

    assert result["offline"] is True
    titles = {note["title"] for note in result["vault_evidence"]}
    assert titles
    # The answer names what it rests on and carries the layer, rather than claiming
    # there is nothing when the vault in fact holds relevant notes.
    assert "vault record" in result["answer"]
    assert any(layer in result["answer"] for layer in ("elicited", "inferred"))


def test_run_turn_without_a_brain_still_reports_an_honest_empty(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    result = run_turn("anything", claims=[], catalogs={})

    assert result["vault_evidence"] == []
    assert "offline" in result["answer"].lower()
