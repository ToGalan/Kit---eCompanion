from fastapi.testclient import TestClient

from app.browser_signal import app, make_session_token


def test_obsidian_brain_api_ingests_and_routes():
    client = TestClient(app)

    ingest = client.post(
        "/brain/ingest",
        json={"title": "The Matrix", "body": "A hero learns the truth inside a simulation."},
        headers={"Authorization": f"Bearer {make_session_token('brain-user')}"},
    )
    assert ingest.status_code == 200
    assert ingest.json()["title"] == "The Matrix"
    assert ingest.json()["kind"] == "works"

    route = client.post("/brain/route", json={"prompt": "What is the central claim of this work?"})
    assert route.status_code == 200
    assert route.json()["route"] == "question"


def test_vault_api_writes_claim_and_requires_falsifier():
    client = TestClient(app)

    created = client.post(
        "/vault/claim",
        json={
            "title": "Claim A",
            "content": "[[The Matrix]] is a hopeful story about escape.",
            "falsifier": "Critic Bob",
            "dimension": "quality",
            "confidence": 0.8,
        },
    )
    assert created.status_code == 200
    assert created.json()["title"] == "Claim A"

    rejected = client.post(
        "/vault/claim",
        json={
            "title": "Bad Claim",
            "content": "This is an unsupported claim.",
            "falsifier": "",
        },
    )
    assert rejected.status_code == 400


def test_auth_session_route_mints_real_bearer_token():
    client = TestClient(app)

    session = client.get("/auth/session")
    assert session.status_code == 200
    payload = session.json()
    token = payload["token"]
    assert token.startswith(f"{payload['user']}.")

    response = client.post("/chat", json={"message": "What should I watch this weekend?"}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["message"]


def test_chat_route_uses_live_backend_path_and_not_a_static_prompt(monkeypatch):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {make_session_token('chat-user')}"}

    seen: dict[str, str] = {}

    def fake_generate(_self, prompt, **_kwargs):
        seen["prompt"] = prompt
        return "Midnight Diner runs about 25 minutes an episode and each one stands alone."

    # Pinned to a stub so the assertion is about the route reaching the gateway, not
    # about whatever the live model happens to reply on the day the suite runs.
    monkeypatch.setattr("app.browser_signal.Opus5Gateway.generate", fake_generate)

    response = client.post("/chat", json={"message": "What should I watch this weekend?"}, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "Midnight Diner" in data["message"]
    assert "What should I watch this weekend?" in seen["prompt"]


def test_chat_route_requires_authentication():
    client = TestClient(app)

    response = client.post("/chat", json={"message": "What should I watch this weekend?"})
    assert response.status_code == 401


def test_chat_route_does_not_leak_backend_error_detail(monkeypatch):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {make_session_token('chat-user')}"}

    def _explode(*_args, **_kwargs):
        raise ConnectionError("sk-ant-secret-key rejected by https://internal.example/v1")

    # Patched at the turn boundary: the route no longer makes a raw model call per
    # message, so the leak has to be prevented where failures actually arrive.
    monkeypatch.setattr("app.browser_signal.run_conversation_turn", _explode)

    response = client.post("/chat", json={"message": "Anything good tonight?"}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "error" not in body
    assert "sk-ant-secret-key" not in response.text


def _chat(client, token, message):
    return client.post(
        "/chat",
        json={"message": message},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_two_sequential_chats_share_conversation_state(monkeypatch):
    """The second turn must see what the first turn said, or it is not a conversation."""
    from app.browser_signal import forget_conversation

    forget_conversation("stateful-user")
    client = TestClient(app)
    token = make_session_token("stateful-user")

    first = _chat(client, token, "I bounced off the last three long RPGs.")
    assert first.status_code == 200

    second = _chat(client, token, "So what should I start tonight?")
    assert second.status_code == 200

    turns = second.json()["state"]["recent_turns"]
    texts = [turn["text"] for turn in turns]
    assert "I bounced off the last three long RPGs." in texts, texts
    assert "So what should I start tonight?" in texts, texts
    # The session counter carries forward rather than resetting on each request.
    assert second.json()["state"]["session_state"]["elicitation_count"] >= 1


def test_conversation_state_is_per_user_not_global():
    from app.browser_signal import forget_conversation

    for name in ("user-a", "user-b"):
        forget_conversation(name)
    client = TestClient(app)

    _chat(client, make_session_token("user-a"), "I only watch horror.")
    reply = _chat(client, make_session_token("user-b"), "What about me?")

    texts = [turn["text"] for turn in reply.json()["state"]["recent_turns"]]
    assert "I only watch horror." not in texts


def test_chat_returns_the_turn_intent():
    from app.browser_signal import forget_conversation

    forget_conversation("intent-user")
    client = TestClient(app)

    body = _chat(client, make_session_token("intent-user"), "hello").json()

    assert body["intent"] in {"ASK", "OFFER", "ANSWER", "ACKNOWLEDGE"}


def test_missing_api_key_is_a_500_not_an_unavailable_message(monkeypatch):
    """A deployment fault must not be indistinguishable from a busy upstream."""
    from mind.ai import ConfigurationError

    def misconfigured(*_args, **_kwargs):
        raise ConfigurationError("Anthropic API key is not configured.")

    monkeypatch.setattr("app.browser_signal.run_conversation_turn", misconfigured)
    client = TestClient(app, raise_server_exceptions=False)

    response = _chat(client, make_session_token("cfg-user"), "anything")
    assert response.status_code == 500


def test_transport_failure_returns_the_unavailable_message(monkeypatch):
    def unreachable(*_args, **_kwargs):
        raise ConnectionError("connection reset")

    monkeypatch.setattr("app.browser_signal.run_conversation_turn", unreachable)
    client = TestClient(app)

    body = _chat(client, make_session_token("net-user"), "anything").json()
    assert body["ok"] is False
    assert "unavailable" in body["message"].lower()


def test_deleting_a_user_drops_their_conversation_state():
    from app.browser_signal import conversation_state_for, store

    client = TestClient(app)
    _chat(client, make_session_token("gone-user"), "Remember this.")
    assert conversation_state_for("gone-user").last_turns

    store.delete_for_user("gone-user")

    assert conversation_state_for("gone-user").last_turns == []


# --- voice retry at the chat boundary -------------------------------------------------

def _decision(text, intent="ANSWER"):
    from mind.conversation import TurnDecision

    return TurnDecision(intent, text, state={})


def test_one_bad_sample_is_rewritten_rather_than_failing_the_turn(monkeypatch):
    import app.browser_signal as api

    monkeypatch.setattr(api, "run_conversation_turn", lambda *a, **k: _decision("I miss you already."))
    rewrites = []

    def rewrite(_self, prompt, **_kwargs):
        rewrites.append(prompt)
        return "Good to pick this up again. What did you finish last?"

    monkeypatch.setattr(api.Opus5Gateway, "generate", rewrite)
    client = TestClient(api.app)

    body = client.post(
        "/chat", json={"message": "hi"},
        headers={"Authorization": f"Bearer {make_session_token('retry-user')}"},
    ).json()

    assert body["ok"] is True
    assert body["message"] == "Good to pick this up again. What did you finish last?"
    # The retry names the rule that was broken.
    assert "claim feelings" in rewrites[0]
    assert "I miss you already." in rewrites[0]


def test_two_strikes_falls_back_without_naming_the_machinery(monkeypatch):
    import app.browser_signal as api

    monkeypatch.setattr(api, "run_conversation_turn", lambda *a, **k: _decision("I miss you already."))
    # The rewrite is out of voice too.
    monkeypatch.setattr(api.Opus5Gateway, "generate", lambda *a, **k: "I love you and I miss you.")
    client = TestClient(api.app)

    body = client.post(
        "/chat", json={"message": "hi"},
        headers={"Authorization": f"Bearer {make_session_token('strike-user')}"},
    ).json()

    assert body["ok"] is False
    assert body["message"] == api.VOICE_FALLBACK
    # Nothing about the guard reaches the user.
    for leak in ("voice", "out of voice", "spec", "pattern", "banned"):
        assert leak not in body["message"].lower()


def test_violations_are_never_returned_to_the_client(monkeypatch):
    import app.browser_signal as api

    monkeypatch.setattr(api, "run_conversation_turn", lambda *a, **k: _decision("I miss you already."))
    monkeypatch.setattr(api.Opus5Gateway, "generate", lambda *a, **k: "I love you.")
    client = TestClient(api.app)

    response = client.post(
        "/chat", json={"message": "hi"},
        headers={"Authorization": f"Bearer {make_session_token('leak-user')}"},
    )

    assert "voice_violations" not in response.json()
    assert "i miss you" not in response.text.lower()


def test_the_fallback_message_itself_passes_the_guard():
    import app.browser_signal as api
    from mind.voice import validate_voice_copy

    assert validate_voice_copy(api.VOICE_FALLBACK) == []


def test_a_clean_answer_is_not_rewritten(monkeypatch):
    import app.browser_signal as api

    monkeypatch.setattr(api, "run_conversation_turn", lambda *a, **k: _decision("Paterson runs 118 minutes."))
    calls = []
    monkeypatch.setattr(api.Opus5Gateway, "generate", lambda *a, **k: calls.append(1) or "unused")
    client = TestClient(api.app)

    body = client.post(
        "/chat", json={"message": "hi"},
        headers={"Authorization": f"Bearer {make_session_token('clean-user')}"},
    ).json()

    assert body["message"] == "Paterson runs 118 minutes."
    assert calls == []
