from datetime import datetime, timezone

from mind.freshness import (
    availability_ok,
    candidate_release_window,
    calendar_relevance,
    verify_candidate,
)


def test_release_window_varies_by_function_and_calendar():
    now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    candidate = {
        "title": "Frieren: Beyond Journey's End",
        "functions": ["social conversation", "current discussion"],
        "release_date": "2026-09-05",
        "release_window": "released_this_month",
        "season": "fall",
    }

    assert candidate_release_window(candidate, now=now) == "released_this_month"
    assert calendar_relevance(candidate, now=now, user_mentions=["anime", "fall"]) > 0


def test_unavailable_title_is_filtered_before_ranking():
    candidate = {
        "title": "Only On Service X",
        "functions": ["comfort", "social"],
        "available_regions": ["US"],
        "services": {"US": ["netflix"]},
        "price": {"currency": "USD", "amount": 0},
        "source": {"title": "Netflix", "url": "https://example.test/netflix"},
    }

    user_services = {"streaming": ["max"], "region": "US"}
    assert availability_ok(candidate, user_services=user_services, region="US") is False


def test_fabricated_title_fails_verification_and_never_renders():
    candidate = {
        "title": "Totally Fake Never Seen Title",
        "functions": ["social conversation"],
        "release_date": "2026-09-08",
        "services": {"US": ["netflix"]},
        "source": {"title": "No real source", "url": "https://example.test/does-not-exist"},
    }

    assert verify_candidate(candidate, user_services={"streaming": ["netflix"], "region": "US"}) is False
