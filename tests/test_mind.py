from __future__ import annotations

import os
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


# --- agentic tool loop ---------------------------------------------------------------

def _text_block(text):
    return Mock(type="text", text=text)


def _tool_use_block(block_id, name, payload):
    # Mock(name=...) sets the mock's own repr name, so .name has to be assigned after.
    block = Mock(type="tool_use", id=block_id, input=payload)
    block.name = name
    return block


def _response(*blocks, stop_reason="end_turn"):
    return Mock(content=list(blocks), stop_reason=stop_reason)


class _RecordingTool:
    name = "steam_catalog"
    description = "Look up Steam store metadata."
    input_schema = {"type": "object", "properties": {"query": {"type": "string"}}}

    def __init__(self, result=None, raises=None):
        self._result = result
        self._raises = raises
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return self._result


def _client_returning(monkeypatch, *responses):
    client = Mock()
    client.messages.create.side_effect = list(responses)
    monkeypatch.setattr("mind.ai.Anthropic", lambda **kwargs: client)
    return client


def test_converse_runs_a_single_tool_round_trip(monkeypatch):
    tool = _RecordingTool(
        result={
            "ok": True,
            "source": "steam_catalog",
            "fetched_at": "2026-09-11T10:00:00+00:00",
            "data": {"title": "Hades"},
        }
    )
    client = _client_returning(
        monkeypatch,
        _response(_tool_use_block("tu_1", "steam_catalog", {"query": "Hades"}), stop_reason="tool_use"),
        _response(_text_block("Hades is on Steam.")),
    )
    gateway = Opus5Gateway(api_key="test-key")

    result = gateway.converse("Is Hades on Steam?", tools={"steam_catalog": tool})

    assert tool.calls == [{"query": "Hades"}]
    assert result.text == "Hades is on Steam."
    assert result.iterations == 2
    assert result.stopped_at_cap is False

    # The schemas actually reached the API, which is the whole point.
    first_call = client.messages.create.call_args_list[0].kwargs
    assert first_call["tools"][0]["name"] == "steam_catalog"

    # And the tool result went back as a tool_result block tied to the call id.
    second_call = client.messages.create.call_args_list[1].kwargs
    blocks = second_call["messages"][-1]["content"]
    assert blocks[0]["type"] == "tool_result"
    assert blocks[0]["tool_use_id"] == "tu_1"
    assert blocks[0]["is_error"] is False


def test_converse_carries_source_and_timestamp_through_to_the_caller(monkeypatch):
    tool = _RecordingTool(result={"ok": True, "data": {"title": "Hades"}})
    _client_returning(
        monkeypatch,
        _response(_tool_use_block("tu_1", "steam_catalog", {"query": "Hades"}), stop_reason="tool_use"),
        _response(_text_block("done")),
    )

    result = Opus5Gateway(api_key="test-key").converse("q", tools={"steam_catalog": tool})

    # Clause 4.2: provenance survives the loop even when the tool omitted it.
    assert len(result.tool_results) == 1
    record = result.tool_results[0]
    assert record["source"] == "steam_catalog"
    assert record["fetched_at"]
    assert result.reached_any_source is True


def test_converse_handles_two_rounds_of_tool_use(monkeypatch):
    tool = _RecordingTool(result={"ok": True, "source": "steam_catalog", "fetched_at": "t", "data": {}})
    _client_returning(
        monkeypatch,
        _response(_tool_use_block("tu_1", "steam_catalog", {"query": "Hades"}), stop_reason="tool_use"),
        _response(_tool_use_block("tu_2", "steam_catalog", {"query": "Hades II"}), stop_reason="tool_use"),
        _response(_text_block("Both exist.")),
    )

    result = Opus5Gateway(api_key="test-key").converse("compare them", tools={"steam_catalog": tool})

    assert [call["query"] for call in tool.calls] == ["Hades", "Hades II"]
    assert result.iterations == 3
    assert len(result.tool_results) == 2
    assert result.text == "Both exist."
    assert result.stopped_at_cap is False


def test_converse_stops_at_the_iteration_cap(monkeypatch):
    tool = _RecordingTool(result={"ok": True, "source": "steam_catalog", "fetched_at": "t", "data": {}})
    # The model never stops asking.
    client = _client_returning(
        monkeypatch,
        *[
            _response(_tool_use_block(f"tu_{i}", "steam_catalog", {"query": str(i)}), stop_reason="tool_use")
            for i in range(10)
        ],
    )

    result = Opus5Gateway(api_key="test-key").converse(
        "loop", tools={"steam_catalog": tool}, max_iterations=3
    )

    assert result.stopped_at_cap is True
    assert result.iterations == 3
    assert client.messages.create.call_count == 3
    assert len(tool.calls) == 3


def test_a_raising_tool_returns_an_error_result_instead_of_failing_the_turn(monkeypatch):
    tool = _RecordingTool(raises=RuntimeError("steam is unreachable"))
    _client_returning(
        monkeypatch,
        _response(_tool_use_block("tu_1", "steam_catalog", {"query": "Hades"}), stop_reason="tool_use"),
        _response(_text_block("I could not reach Steam just now.")),
    )

    result = Opus5Gateway(api_key="test-key").converse("q", tools={"steam_catalog": tool})

    # Clause 4.7: the domain drops out, the turn survives.
    assert result.text == "I could not reach Steam just now."
    record = result.tool_results[0]
    assert record["ok"] is False
    assert "steam is unreachable" in record["error"]
    assert record["source"] == "steam_catalog"
    assert record["fetched_at"]
    assert result.reached_any_source is False


def test_an_unknown_tool_name_is_reported_not_raised(monkeypatch):
    _client_returning(
        monkeypatch,
        _response(_tool_use_block("tu_1", "nonexistent", {}), stop_reason="tool_use"),
        _response(_text_block("no such source")),
    )

    result = Opus5Gateway(api_key="test-key").converse("q", tools={"steam_catalog": _RecordingTool()})

    assert result.tool_results[0]["ok"] is False
    assert "unknown tool" in result.tool_results[0]["error"]


def test_mixed_content_does_not_break_text_extraction(monkeypatch):
    """A response carrying thinking and tool_use alongside text must still yield the text."""
    thinking = Mock(type="thinking", thinking="considering", signature="sig")
    _client_returning(
        monkeypatch,
        _response(
            thinking,
            _text_block("Checking Steam."),
            _tool_use_block("tu_1", "steam_catalog", {"query": "Hades"}),
            stop_reason="tool_use",
        ),
        _response(_text_block("Hades is on Steam.")),
    )
    tool = _RecordingTool(result={"ok": True, "source": "steam_catalog", "fetched_at": "t", "data": {}})

    result = Opus5Gateway(api_key="test-key").converse("q", tools={"steam_catalog": tool})

    assert result.text == "Hades is on Steam."
    # The thinking block is echoed back rather than dropped.
    assistant = [m for m in result.messages if m["role"] == "assistant"][0]
    kinds = [block["type"] for block in assistant["content"]]
    assert kinds == ["thinking", "text", "tool_use"]


def test_converse_without_tools_sends_no_tools_key(monkeypatch):
    client = _client_returning(monkeypatch, _response(_text_block("plain answer")))

    result = Opus5Gateway(api_key="test-key").converse("hello")

    assert result.text == "plain answer"
    assert result.tool_results == []
    assert "tools" not in client.messages.create.call_args.kwargs


def test_generate_and_generate_structured_still_send_no_tools(monkeypatch):
    """Single-shot callers must be unaffected by the tool support."""
    client = _client_returning(
        monkeypatch,
        _response(_text_block("single shot")),
        _response(_text_block('{"ok": true}')),
    )
    gateway = Opus5Gateway(api_key="test-key")

    assert gateway.generate("hi") == "single shot"
    assert gateway.generate_structured("hi", {"type": "object"}) == {"ok": True}
    for call in client.messages.create.call_args_list:
        assert "tools" not in call.kwargs
