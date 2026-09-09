from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _iso_to_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                return datetime.fromtimestamp(float(text), tz=timezone.utc)
            except (TypeError, ValueError):
                return None
    return None


def _candidate_key(candidate: dict[str, Any], field: str, default: Any = None) -> Any:
    return candidate.get(field, default)


def _function_tokens(candidate: dict[str, Any]) -> list[str]:
    raw = []
    for key in ("functions", "function", "function_served", "occasion", "why_it_fits"):
        value = candidate.get(key)
        if isinstance(value, str):
            raw.append(value)
        elif isinstance(value, list):
            raw.extend(str(part) for part in value)
    combined = " ".join(raw).lower()
    return [token for token in combined.replace("-", " ").split() if token]


def candidate_release_window(candidate: dict[str, Any], *, now: datetime | None = None) -> str:
    """Return the release-window bucket for a candidate.

    The release window is supplied by the metadata layer and can be overridden by a
    function-specific model when the occasion demands currentness, but it is still
    applied at the candidate level rather than as a blanket recency penalty.
    """
    if not isinstance(candidate, dict):
        return "back_catalogue"

    explicit = str(_candidate_key(candidate, "release_window") or "").strip().lower()
    if explicit in {"airing_now", "released_this_month", "released_this_year", "back_catalogue"}:
        return explicit

    release_date = _candidate_key(candidate, "release_date") or _candidate_key(candidate, "released_at") or _candidate_key(candidate, "first_release_date") or _candidate_key(candidate, "airing_date")
    dt = _iso_to_datetime(release_date)
    if dt is None:
        return "back_catalogue"

    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_days = (reference - dt).total_seconds() / 86400.0
    if age_days <= 30:
        return "released_this_month"
    if age_days <= 365:
        return "released_this_year"
    return "back_catalogue"


def calendar_relevance(candidate: dict[str, Any], *, now: datetime | None = None, user_mentions: list[str] | None = None) -> float:
    """Score how relevant a candidate is for seasonal or time-sensitive occasions."""
    if not isinstance(candidate, dict):
        return 0.0

    score = 0.0
    release_window = candidate_release_window(candidate, now=now)
    function_tokens = _function_tokens(candidate)

    current_functions = {"social", "conversation", "talk", "current", "discuss", "trending", "event", "seasonal", "buzz"}
    indifferent_functions = {"comfort", "calm", "rest", "regulation", "recovery", "decompression"}

    if current_functions & set(function_tokens):
        weights = {
            "airing_now": 0.85,
            "released_this_month": 0.55,
            "released_this_year": 0.35,
            "back_catalogue": 0.15,
        }
        score += weights.get(release_window, 0.0)
    elif indifferent_functions & set(function_tokens):
        weights = {
            "airing_now": 0.12,
            "released_this_month": 0.15,
            "released_this_year": 0.2,
            "back_catalogue": 0.55,
        }
        score += weights.get(release_window, 0.0)
    else:
        score += 0.2 if release_window in {"airing_now", "released_this_month"} else 0.1

    mentions = [str(item).lower() for item in (user_mentions or [])]
    season = str(candidate.get("season") or candidate.get("anime_season") or "").lower()
    if season:
        score += 0.25 if any(token in " ".join(mentions) for token in [season, "season"]) or season in " ".join(mentions) else 0.08

    holiday_tokens = {
        "christmas": ["christmas", "holiday", "festive"],
        "new_year": ["new year", "newyear", "year end"],
        "summer": ["summer", "vacation"],
        "fall": ["fall", "autumn"],
        "winter": ["winter", "cold season"],
    }
    if any(token in " ".join(mentions) or token in season for token in ["holiday", "anime", "event", "festival", "convention"]):
        score += 0.1
    for token, values in holiday_tokens.items():
        if token in " ".join(mentions) or any(value in " ".join(mentions) for value in values):
            score += 0.12

    return max(0.0, min(1.0, score))


def availability_ok(candidate: dict[str, Any], *, user_services: dict[str, Any] | None = None, region: str | None = None) -> bool:
    """Check whether the candidate is available to the user in the stated service region."""
    if not isinstance(candidate, dict):
        return False

    normalized_region = (region or (user_services or {}).get("region") or "").strip()
    if candidate.get("available_regions"):
        regions = {str(item).strip() for item in candidate["available_regions"]}
        if normalized_region and normalized_region not in regions and "global" not in regions:
            return False

    services_map = candidate.get("services") or {}
    if isinstance(services_map, dict):
        allowed_services = set()
        if normalized_region:
            allowed_services.update(str(item) for item in services_map.get(normalized_region, []))
        allowed_services.update(str(item) for item in services_map.get("global", []))
        allowed_services.update(str(item) for item in services_map.get("all", []))
        if allowed_services:
            user_allowed = set()
            for key in ("streaming", "services", "owned_services", "purchases", "has_access_to"):
                value = (user_services or {}).get(key)
                if isinstance(value, str):
                    user_allowed.add(value)
                elif isinstance(value, list):
                    user_allowed.update(str(item) for item in value)
            if user_allowed and not user_allowed.intersection(allowed_services):
                return False

    purchase = candidate.get("purchase_status") or candidate.get("availability") or candidate.get("status")
    if isinstance(purchase, str):
        lowered = purchase.lower()
        if "unavailable" in lowered or "not available" in lowered or "removed" in lowered:
            return False
    if candidate.get("is_purchasable") is False or candidate.get("is_streaming") is False:
        return False

    return True


def verify_candidate(candidate: dict[str, Any], *, user_services: dict[str, Any] | None = None, region: str | None = None, now: datetime | None = None) -> bool:
    """Reject model-generated titles that cannot be proven by a real source."""
    if not isinstance(candidate, dict):
        return False

    title = str(candidate.get("title") or "").strip()
    if not title or title.lower() in {"unknown", "tbd", "n/a", "not found"}:
        return False

    if not availability_ok(candidate, user_services=user_services, region=region):
        return False

    source = candidate.get("source") or candidate.get("source_info") or candidate.get("source_url")
    if isinstance(source, dict):
        url = str(source.get("url") or source.get("href") or source.get("link") or "").strip()
        if not url or not url.startswith(("http://", "https://")):
            return False
    elif isinstance(source, str):
        if not source.startswith(("http://", "https://")):
            return False
    else:
        return False

    fetched_at = (
        candidate.get("fetched_at")
        or candidate.get("last_verified")
        or (isinstance(source, dict) and source.get("fetched_at"))
    )
    if fetched_at is None or _iso_to_datetime(fetched_at) is None:
        return False

    return True
