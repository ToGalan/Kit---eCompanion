from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from kit.vault import Occasion


@dataclass
class OccasionInference:
    occasion: Occasion
    confidence: float
    inferred_fields: list[str] = field(default_factory=list)
    stated_fields: list[str] = field(default_factory=list)


def infer_time_of_day(timestamp: datetime) -> str:
    hour = timestamp.hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "night"


def infer_day_type(timestamp: datetime) -> str:
    return "weekend" if timestamp.weekday() >= 5 else "weekday"


def infer_session_length_from_observations(observations: list[float]) -> str:
    if len(observations) < 5:
        return "unknown"
    durations = sorted(float(value) for value in observations)
    median = durations[len(durations) // 2]
    if median < 15:
        return "short"
    if median < 60:
        return "medium"
    return "open"


def build_occasion_from_timestamp(
    timestamp: datetime,
    *,
    session_lengths: list[float] | None = None,
    user_label: str | None = None,
) -> Occasion:
    session_length = infer_session_length_from_observations(session_lengths or [])
    occasion = Occasion(
        time_of_day=infer_time_of_day(timestamp),
        day_type=infer_day_type(timestamp),
        session_length="short" if session_length == "unknown" else session_length,
        label=user_label,
    )
    if session_length == "unknown":
        occasion.session_length = "unknown"
    return occasion


def current_occasion(user: Any, *, now: datetime | None = None, session_lengths: list[float] | None = None, store: Any | None = None) -> OccasionInference:
    if store is None:
        from app.browser_signal import BrowserSignalStore

        store = BrowserSignalStore()

    user_record = store.user_record(user)
    tz_name = user_record.get("timezone")
    if not tz_name:
        raise ValueError("user timezone is required to infer the current occasion")

    tzinfo = ZoneInfo(tz_name)
    reference_now = now or datetime.now(tzinfo)
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=tzinfo)
    else:
        reference_now = reference_now.astimezone(tzinfo)

    events = user_record.get("events", [])
    same_bucket = []
    for event in events:
        ts = event.get("timestamp")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tzinfo)
        dt = dt.astimezone(tzinfo)
        if infer_day_type(dt) == infer_day_type(reference_now) and infer_time_of_day(dt) == infer_time_of_day(reference_now):
            duration = event.get("session_duration_seconds")
            if isinstance(duration, (int, float)):
                same_bucket.append(float(duration))

    effective_lengths = same_bucket if session_lengths is None else session_lengths
    occasion = build_occasion_from_timestamp(reference_now, session_lengths=effective_lengths, user_label=None)

    inferred_fields: list[str] = ["time_of_day", "day_type"]
    stated_fields: list[str] = ["timezone"]
    if occasion.session_length != "unknown":
        inferred_fields.append("session_length")
    confidence = 0.95 if occasion.session_length != "unknown" else 0.7

    return OccasionInference(occasion=occasion, confidence=confidence, inferred_fields=inferred_fields, stated_fields=stated_fields)
