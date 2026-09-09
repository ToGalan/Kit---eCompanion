from fastapi.testclient import TestClient

from app.metrics import ExposureMetricStore, app, compute_exposure_metrics, evaluate_exposure_health, make_admin_token


def test_exposure_metrics_capture_distribution_and_mark_failure_when_gini_too_high(tmp_path):
    store = ExposureMetricStore(db_path=tmp_path / "exposure.db")

    for _ in range(95):
        store.log_recommendation("Title A", "spotify.com", "user-1", engaged=True)
    for _ in range(4):
        store.log_recommendation("Title B", "spotify.com", "user-2", engaged=False)
    for _ in range(1):
        store.log_recommendation("Title C", "netflix.com", "user-3", engaged=True)

    metrics = compute_exposure_metrics(store, window_days=30)
    assert metrics["total_recommendations"] == 100
    assert metrics["distinct_titles"] == 3
    assert metrics["gini"] > 0.60
    assert metrics["top_1_percent_share"] >= 0.0

    health = evaluate_exposure_health(metrics, gini_ceiling=0.60)
    assert health["status"] == "fail"
    assert "gini" in health["message"].lower()


def test_metrics_route_requires_admin_auth(tmp_path):
    store = ExposureMetricStore(db_path=tmp_path / "metrics.db")
    app.state.metrics_store = store

    store.log_recommendation("Title A", "spotify.com", "user-a", engaged=True)
    store.log_recommendation("Title B", "spotify.com", "user-b", engaged=False)

    client = TestClient(app)
    admin_token = make_admin_token("admin")
    response = client.get("/api/metrics/exposure", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_recommendations"] >= 2
    assert payload["distinct_titles"] >= 2
    assert "gini" in payload
    assert "top_1_percent_share" in payload
    assert "long_tail_reach" in payload

    unauth = client.get("/api/metrics/exposure")
    assert unauth.status_code == 401
