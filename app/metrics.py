from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException



def make_admin_token(user: str = "admin") -> str:
    secret = os.environ.get("KIT_ADMIN_SECRET", "kit-admin-secret")
    digest = hmac.new(secret.encode("utf-8"), user.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{user}.{digest}"


def resolve_admin_from_token(token: str) -> str | None:
    if not token:
        return None
    try:
        user, digest = token.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(os.environ.get("KIT_ADMIN_SECRET", "kit-admin-secret").encode("utf-8"), user.encode("utf-8"), hashlib.sha256).hexdigest()
    if hmac.compare_digest(digest, expected):
        return user
    return None


def get_admin_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")
    token = authorization.split(" ", 1)[1].strip()
    user = resolve_admin_from_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid bearer token")
    return user


class ExposureMetricStore:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or os.environ.get("KIT_METRICS_DB_PATH", "./data/exposure.db"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS exposure_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    user TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    engaged INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def log_recommendation(self, title: str, domain: str, user: str, *, engaged: bool, timestamp: datetime | None = None) -> dict[str, Any]:
        ts = (timestamp or datetime.now(timezone.utc)).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO exposure_events (title, domain, user, timestamp, engaged) VALUES (?, ?, ?, ?, ?)",
                (title, domain, user, ts, int(bool(engaged))),
            )
        return {"id": cursor.lastrowid, "title": title, "domain": domain, "user": user, "timestamp": ts, "engaged": bool(engaged)}

    def _window_start(self, *, window_days: int) -> datetime:
        return datetime.now(timezone.utc) - timedelta(days=window_days)

    def recent_events(self, *, window_days: int = 30) -> list[dict[str, Any]]:
        cutoff = self._window_start(window_days=window_days)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT title, domain, user, timestamp, engaged FROM exposure_events WHERE timestamp >= ? ORDER BY timestamp ASC",
                (cutoff.isoformat(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM exposure_events")


def _gini(values: list[float]) -> float:
    if not values:
        return 0.0
    values = sorted(float(value) for value in values)
    total = sum(values)
    if total <= 0:
        return 0.0
    n = len(values)
    weighted_sum = 0.0
    for i, value in enumerate(values, start=1):
        weighted_sum += (2 * i - n - 1) * value
    return weighted_sum / (n * total)


def compute_exposure_metrics(store: ExposureMetricStore, *, window_days: int = 30) -> dict[str, Any]:
    events = store.recent_events(window_days=window_days)
    counts: dict[str, int] = {}
    for event in events:
        title = str(event["title"]).strip()
        if not title:
            continue
        counts[title] = counts.get(title, 0) + 1

    total_recommendations = sum(counts.values())
    if total_recommendations == 0:
        return {
            "window_days": window_days,
            "total_recommendations": 0,
            "gini": 0.0,
            "top_1_percent_share": 0.0,
            "long_tail_reach": 0,
            "distinct_titles": 0,
            "titles": [],
        }

    exposure_values = list(counts.values())
    gini = _gini(exposure_values)
    top_share = 0.0
    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    if ranked:
        top_1_percent_count = max(1, int(math.ceil(len(ranked) * 0.01)))
        top_total = sum(count for _, count in ranked[:top_1_percent_count])
        top_share = top_total / total_recommendations

    return {
        "window_days": window_days,
        "total_recommendations": total_recommendations,
        "gini": round(gini, 6),
        "top_1_percent_share": round(top_share, 6),
        "long_tail_reach": len(counts),
        "distinct_titles": len(counts),
        "titles": [{"title": title, "exposure": count} for title, count in ranked],
    }


def evaluate_exposure_health(metrics: dict[str, Any], *, gini_ceiling: float = 0.75) -> dict[str, Any]:
    gini = float(metrics.get("gini", 0.0))
    if gini > gini_ceiling:
        return {
            "status": "fail",
            "message": f"Exposure Gini ({gini:.3f}) exceeded the configured ceiling ({gini_ceiling:.3f}); concentration is too close to the market baseline the product is designed to correct.",
            "gini_ceiling": gini_ceiling,
            "gini": gini,
        }
    return {
        "status": "pass",
        "message": f"Exposure Gini ({gini:.3f}) is within the configured ceiling ({gini_ceiling:.3f}).",
        "gini_ceiling": gini_ceiling,
        "gini": gini,
    }


store = ExposureMetricStore()
app = FastAPI(title="Kit Metrics")
app.state.metrics_store = store


@app.get("/api/metrics/exposure")
def exposure_metrics(admin: str = Depends(get_admin_user)):
    metrics_store = getattr(app.state, "metrics_store", store)
    metrics = compute_exposure_metrics(metrics_store)
    metrics["health"] = evaluate_exposure_health(metrics)
    return metrics


if "math" not in globals():
    import math
