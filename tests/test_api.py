from fastapi.testclient import TestClient

from app.browser_signal import app


def test_obsidian_brain_api_ingests_and_routes():
    client = TestClient(app)

    ingest = client.post(
        "/brain/ingest",
        json={"title": "The Matrix", "body": "A hero learns the truth inside a simulation."},
    )
    assert ingest.status_code == 200
    assert ingest.json()["title"] == "The Matrix"

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
