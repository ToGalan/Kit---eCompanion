from fastapi.testclient import TestClient

from app.browser_signal import BrowserSignalStore, app, MEDIA_DOMAINS


def test_single_visit_is_not_evidence():
    store = BrowserSignalStore()
    store.record("alice", "youtube.com", "save")
    patterns = store.detect_user_patterns("alice")
    assert patterns == {}

    store.record("alice", "youtube.com", "save")
    patterns = store.detect_user_patterns("alice")
    assert "youtube.com" in patterns
    assert any(signal in patterns["youtube.com"] for signal in {"save", "bounce", "circle"})


def test_delete_history_removes_everything_it_contributed():
    client = TestClient(app)

    client.post("/history/alice", json={"domain": "youtube.com", "signal": "save"})
    client.post("/history/alice", json={"domain": "netflix.com", "signal": "circle"})
    client.post("/history/alice", json={"domain": "youtube.com", "signal": "save"})

    deleted = client.delete("/history/alice")
    assert deleted.status_code == 200

    history = client.get("/history/alice")
    assert history.status_code == 200
    assert history.json()["events"] == []
    assert history.json()["patterns"] == {}


def test_media_domains_are_named_and_one_site_per_context():
    assert len(MEDIA_DOMAINS) == 15
    assert "youtube.com" in MEDIA_DOMAINS
    assert "netflix.com" in MEDIA_DOMAINS
    assert "steamcommunity.com" in MEDIA_DOMAINS
