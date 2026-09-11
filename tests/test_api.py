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
        raise RuntimeError("sk-ant-secret-key rejected by https://internal.example/v1")

    monkeypatch.setattr("app.browser_signal.Opus5Gateway.generate", _explode)

    response = client.post("/chat", json={"message": "Anything good tonight?"}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "error" not in body
    assert "sk-ant-secret-key" not in response.text
