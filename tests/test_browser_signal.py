from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.browser_signal import BrowserSignalStore, app, MEDIA_DOMAINS, make_session_token


def test_single_visit_is_not_evidence(tmp_path):
    store = BrowserSignalStore(db_path=tmp_path / "browser.db")
    store.record("alice", "youtube.com", "save")
    patterns = store.detect_user_patterns("alice")
    assert patterns == {}

    store.record("alice", "youtube.com", "save")
    patterns = store.detect_user_patterns("alice")
    assert "youtube.com" in patterns
    assert any(signal in patterns["youtube.com"] for signal in {"save", "bounce", "circle"})


def test_delete_history_removes_everything_it_contributed():
    client = TestClient(app)
    alice_token = make_session_token("alice")

    client.post("/history", json={"domain": "youtube.com", "signal": "save"}, headers={"Authorization": f"Bearer {alice_token}"})
    client.post("/history", json={"domain": "netflix.com", "signal": "circle"}, headers={"Authorization": f"Bearer {alice_token}"})
    client.post("/history", json={"domain": "youtube.com", "signal": "save"}, headers={"Authorization": f"Bearer {alice_token}"})

    deleted = client.delete("/history", headers={"Authorization": f"Bearer {alice_token}"})
    assert deleted.status_code == 200

    history = client.get("/history", headers={"Authorization": f"Bearer {alice_token}"})
    assert history.status_code == 200
    assert history.json()["events"] == []
    assert history.json()["patterns"] == {}


def test_user_a_cannot_read_or_delete_user_b_history():
    client = TestClient(app)
    alice_token = make_session_token("alice")
    bob_token = make_session_token("bob")

    resp = client.post(
        "/history",
        json={"domain": "youtube.com", "signal": "save"},
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert resp.status_code == 200

    alice_history = client.get("/history", headers={"Authorization": f"Bearer {alice_token}"})
    assert alice_history.status_code == 200
    assert len(alice_history.json()["events"]) == 1

    bob_history = client.get("/history", headers={"Authorization": f"Bearer {bob_token}"})
    assert bob_history.status_code == 200
    assert bob_history.json()["events"] == []

    bob_delete = client.delete("/history", headers={"Authorization": f"Bearer {bob_token}"})
    assert bob_delete.status_code == 200

    alice_history_after = client.get("/history", headers={"Authorization": f"Bearer {alice_token}"})
    assert alice_history_after.status_code == 200
    assert len(alice_history_after.json()["events"]) == 1


def test_history_requires_valid_bearer_token():
    client = TestClient(app)

    resp = client.get("/history")
    assert resp.status_code == 401

    resp = client.get("/history", headers={"Authorization": "Bearer bad-token"})
    assert resp.status_code == 401


def test_history_records_timezone_sequence_and_previous_outcome(tmp_path):
    store = BrowserSignalStore(db_path=tmp_path / "browser.db")
    store.set_user_timezone("alice", "America/New_York")

    first = store.record(
        "alice",
        "youtube.com",
        "save",
        timestamp="2024-03-05T18:15:00-05:00",
    )
    second = store.record(
        "alice",
        "spotify.com",
        "bounce",
        timestamp="2024-03-05T18:20:00-05:00",
    )

    assert first["timezone"] == "America/New_York"
    assert second["previous_domain"] == "youtube.com"
    assert second["previous_outcome"] == "completed"

    history = store.history_for("alice")["events"]
    assert history[0]["timestamp"].endswith("-05:00")
    assert history[1]["previous_domain"] == "youtube.com"
    assert history[1]["previous_outcome"] == "completed"


def test_current_occasion_marks_inferred_fields_and_avoid_guessing_before_enough_data(tmp_path):
    store = BrowserSignalStore(db_path=tmp_path / "browser.db")
    store.set_user_timezone("alice", "America/New_York")
    for i in range(3):
        store.record("alice", "youtube.com", "save", timestamp=f"2024-03-05T18:{10 + i:02d}:00-05:00")

    occasion = store.current_occasion("alice")
    assert occasion.occasion.session_length == "unknown"
    assert "session_length" not in occasion.inferred_fields
    assert "timezone" in occasion.stated_fields
    assert "time_of_day" in occasion.inferred_fields


def test_media_domains_are_named_and_one_site_per_context():
    assert len(MEDIA_DOMAINS) == 15
    assert "youtube.com" in MEDIA_DOMAINS
    assert "netflix.com" in MEDIA_DOMAINS
    assert "steamcommunity.com" in MEDIA_DOMAINS
