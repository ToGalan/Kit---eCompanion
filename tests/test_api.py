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

    seen: dict[str, object] = {}

    def fake_converse(_self, messages, **kwargs):
        seen["messages"] = messages
        seen["system"] = kwargs.get("system")
        return "Midnight Diner runs about 25 minutes an episode and each one stands alone."

    # Pinned to a stub so the assertion is about the route reaching the gateway, not
    # about whatever the live model happens to reply on the day the suite runs.
    monkeypatch.setattr("app.browser_signal.Opus5Gateway.converse", fake_converse)

    response = client.post("/chat", json={"message": "What should I watch this weekend?"}, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "Midnight Diner" in data["message"]
    assert seen["messages"][-1] == {"role": "user", "content": "What should I watch this weekend?"}


def test_chat_route_requires_authentication():
    client = TestClient(app)

    response = client.post("/chat", json={"message": "What should I watch this weekend?"})
    assert response.status_code == 401


def test_chat_route_does_not_leak_backend_error_detail(monkeypatch):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {make_session_token('chat-user')}"}

    def _explode(*_args, **_kwargs):
        raise RuntimeError("sk-ant-secret-key rejected by https://internal.example/v1")

    monkeypatch.setattr("app.browser_signal.Opus5Gateway.converse", _explode)

    response = client.post("/chat", json={"message": "Anything good tonight?"}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "error" not in body
    assert "sk-ant-secret-key" not in response.text


def test_chat_route_replays_earlier_turns_and_the_vault_state(monkeypatch):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {make_session_token('memory-user')}"}
    seen: dict[str, object] = {}

    def fake_converse(_self, messages, **kwargs):
        seen["messages"] = messages
        seen["system"] = kwargs.get("system")
        return "A 25-minute loop fits that."

    monkeypatch.setattr("app.browser_signal.Opus5Gateway.converse", fake_converse)

    first = client.post(
        "/chat",
        json={"message": "I usually play something short on a weeknight.", "session_id": "memory-user-test"},
        headers=headers,
    )
    assert first.json()["memory"]["recorded"] is True

    client.post("/chat", json={"message": "What should I start tonight?", "session_id": "memory-user-test"}, headers=headers)

    # The second call carries the first exchange as real turns, not as a pasted summary.
    assert seen["messages"][0] == {"role": "user", "content": "I usually play something short on a weeknight."}
    assert seen["messages"][1] == {"role": "assistant", "content": "A 25-minute loop fits that."}
    assert seen["messages"][-1] == {"role": "user", "content": "What should I start tonight?"}
    # And what the vault holds, with the layer it came from.
    assert "[elicited] I usually play something short on a weeknight." in seen["system"]


def test_chat_memory_route_returns_what_was_recorded(monkeypatch):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {make_session_token('recall-user')}"}
    monkeypatch.setattr(
        "app.browser_signal.Opus5Gateway.converse",
        lambda _self, messages, **_kwargs: "Noted.",
    )

    client.post("/chat", json={"message": "I like slow films.", "session_id": "recall-user-test"}, headers=headers)

    recalled = client.get("/chat/memory", params={"session_id": "recall-user-test"}, headers=headers)
    assert recalled.status_code == 200
    payload = recalled.json()
    assert [turn["text"] for turn in payload["turns"]] == ["I like slow films.", "Noted."]
    assert payload["facts"][0]["layer"] == "elicited"


def test_recorded_signal_compiles_into_observed_persona_facts():
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {make_session_token('signal-user')}"}

    for _ in range(2):
        response = client.post(
            "/history",
            json={"domain": "bandcamp.com", "signal": "save", "timezone": "UTC"},
            headers=headers,
        )
        assert response.status_code == 200

    observed = response.json()["observed_facts"]
    assert observed and observed[0]["layer"] == "observed"

    recalled = client.get("/chat/memory", headers=headers).json()
    assert any(fact["layer"] == "observed" for fact in recalled["facts"])
