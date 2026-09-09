from __future__ import annotations

from mind.return_value import (
    Recommendation,
    RetentionState,
    build_notification,
    compounding_value,
    earned_anticipation,
    occasion_ready_for_recommendation,
    reentry_opening,
)


def test_compounding_value_shows_measurable_improvement():
    earlier = {"elicited": [{"title": "likes calm evenings", "content": "quiet media after work"}], "active_hypotheses": [{"confidence": 0.52}]}
    later = {"elicited": [{"title": "likes calm evenings", "content": "quiet media after work"}, {"title": "bounced from long RPGs", "content": "no 60-hour campaign"}], "active_hypotheses": [{"confidence": 0.89}]}

    result = compounding_value(later, previous_snapshot=earlier, occasion={"time_of_day": "evening", "day_type": "weekday", "session_length": "short"})

    assert result.improved is True
    assert result.delta > 0.0
    assert result.current_confidence > result.previous_confidence


def test_earned_anticipation_requires_real_confidence_and_not_yet_seen_titles():
    candidates = [
        {"title": "Quiet Drift", "confidence": 0.91, "reason": "short, low-stimulation evening reset", "wrong_if": "if you want a high-energy challenge loop"},
        {"title": "Night Echo", "confidence": 0.72, "reason": "good, but not strong enough", "wrong_if": "if you want quiet recovery"},
    ]

    matches = earned_anticipation({"active_hypotheses": [{"confidence": 0.88}]}, candidates, seen_titles={"Night Echo"})

    assert [item.title for item in matches] == ["Quiet Drift"]
    assert matches[0].confidence >= 0.8


def test_reentry_opening_is_warm_and_has_no_guilt_or_penalty():
    opening = reentry_opening(days_since_contact=21)

    assert "It’s been 21 days" in opening
    assert "catch-up burden" not in opening.lower()
    assert "guilt" not in opening.lower()
    assert "lost progress" not in opening.lower()


def test_occasion_aware_timing_uses_open_slot_not_scheduled_payloads():
    snapshot = {"active_hypotheses": [{"function": "homebody calm", "confidence": 0.75}]}
    assert occasion_ready_for_recommendation(snapshot, occasion={"time_of_day": "evening", "day_type": "weekday", "session_length": "short"}, kind="homebody calm") is True

    notification = build_notification(Recommendation(title="Quiet Drift", reason="short calm evening reset", confidence=0.91, wrong_if="if you want a challenge loop"))
    assert notification["scheduled"] is False
    assert notification["recommendation"] == "Quiet Drift"


def test_dependency_mechanics_are_absent_from_state_and_notifications():
    state = RetentionState()
    payload = state.as_dict()

    assert "streak" not in payload
    assert "loss_penalty" not in payload
    assert "decay" not in payload
    assert "absence_penalty" not in payload

    try:
        build_notification({"title": "Ping", "reason": "check in", "confidence": 0.9, "wrong_if": "if you want a different fit."}, schedule="daily")
        assert False, "scheduled notifications should be rejected"
    except ValueError:
        pass
