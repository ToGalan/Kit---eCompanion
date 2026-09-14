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


def test_chat_route_hands_the_model_only_verified_titles(monkeypatch):
    """4.5: the model may name what a provider confirmed, and nothing else."""
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {make_session_token('offer-user')}"}
    seen: dict[str, object] = {}

    def fake_converse(_self, messages, **kwargs):
        seen["system"] = kwargs.get("system")
        return "Hades fits a short weeknight. It would be wrong if you wanted something calmer."

    monkeypatch.setattr("app.browser_signal.Opus5Gateway.converse", fake_converse)

    class _Steam:
        """Behaves like the real tool: a query searches, an app id returns detail."""

        name = "steam_catalog"

        def run(self, **kwargs):
            envelope = {
                "tool": "steam_catalog",
                "source": "steam_catalog",
                "fetched_at": "2026-09-14T10:00:00+00:00",
                "args": kwargs,
                "ok": True,
            }
            if kwargs.get("app_id"):
                return envelope | {
                    "data": {
                        "1145360": {
                            "success": True,
                            "data": {
                                "steam_appid": 1145360,
                                "name": "Hades",
                                "short_description": "A cozy, relaxing run-based game.",
                                "genres": [{"description": "Indie"}],
                                "release_date": {"coming_soon": False, "date": "17 Sep, 2020"},
                            },
                        }
                    }
                }
            return envelope | {"data": {"items": [{"id": 1145360, "name": "Hades"}]}}

    monkeypatch.setattr("mind.candidates.DEFAULT_TOOLS", {"steam_catalog": _Steam()}, raising=False)
    monkeypatch.setattr("mind.tools.DEFAULT_TOOLS", {"steam_catalog": _Steam()})

    # A first turn so the persona is not empty: with nothing recorded there is nothing
    # to rank against, and an honest empty is the correct answer (8.2).
    client.post(
        "/chat",
        json={"message": "I like cozy low-stimulation games when I want to decompress.", "session_id": "offer-user-test"},
        headers=headers,
    )

    response = client.post(
        "/chat",
        json={"message": "what should i play tonight? something cozy", "session_id": "offer-user-test"},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    system = str(seen["system"])
    assert "Hades" in system
    assert "https://store.steampowered.com/app/1145360/" in system
    assert "You may name these and nothing else" in system
    # The retrieved shortlist is reported back, so the UI can show what it rested on.
    assert [item["title"] for item in body["candidates"]] == ["Hades"]


def test_chat_route_tells_the_model_to_stay_empty_when_nothing_verifies(monkeypatch):
    """8.2: an honest empty is correct output; a remembered title is not."""
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {make_session_token('empty-user')}"}
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        "app.browser_signal.Opus5Gateway.converse",
        lambda _self, messages, **kwargs: seen.update(system=kwargs.get("system")) or "I don't have one yet.",
    )

    class _Dead:
        name = "steam_catalog"

        def run(self, **_kwargs):
            raise RuntimeError("provider unreachable")

    monkeypatch.setattr("mind.tools.DEFAULT_TOOLS", {"steam_catalog": _Dead()})

    response = client.post(
        "/chat",
        json={"message": "recommend me a game", "session_id": "empty-user-test"},
        headers=headers,
    )

    system = str(seen["system"])
    assert "No title could be confirmed" in system
    assert "never hedged" in system
    assert response.json()["candidates"] == []
