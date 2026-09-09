from __future__ import annotations

from pathlib import Path

from kit.vault import Vault
from mind.conversation import ConversationState, build_model_context, decide_turn


def test_direct_question_takes_priority_over_question_budget():
    state = ConversationState(user="alice")
    decision = decide_turn(
        "What should I listen to this evening?",
        user="alice",
        state=state,
        persona_snapshot={"elicited": [{"title": "quiet drift", "content": "I like calm low-stimulation music"}]},
        occasion={"time_of_day": "evening", "day_type": "weekday", "session_length": "short"},
    )

    assert decision.intent == "ANSWER"
    assert "listen" in decision.message.lower() or "evening" in decision.message.lower()


def test_elicitation_cap_is_session_wide_and_stops_terminal_questioning():
    state = ConversationState(user="alice", elicitation_count=3)
    decision = decide_turn(
        "I am just checking in.",
        user="alice",
        state=state,
        persona_snapshot={"elicited": [{"title": "quiet drift", "content": "I like calm low-stimulation music"}]},
        occasion={"time_of_day": "evening", "day_type": "weekday", "session_length": "short"},
    )

    assert decision.intent in {"ACKNOWLEDGE", "OFFER"}
    assert decision.intent != "ASK"


def test_declined_question_is_not_reasked_in_same_session():
    state = ConversationState(
        user="alice",
        asked=["What did you play last night?"],
        declined=["What did you play last night?"],
    )

    decision = decide_turn(
        "I do not want to answer that question.",
        user="alice",
        state=state,
        persona_snapshot={"elicited": [{"title": "quiet drift", "content": "I prefer low-stimulation media"}]},
        occasion={"time_of_day": "evening", "day_type": "weekday", "session_length": "short"},
    )

    assert decision.intent == "ACKNOWLEDGE"


def test_rejection_of_offer_uses_specific_wrong_if_follow_up_and_writes_to_vault(tmp_path):
    vault = Vault(tmp_path / "vault")
    state = ConversationState(
        user="alice",
        pending_offer={
            "title": "Quiet Drift",
            "wrong_if": "if you want a challenge loop and a loud social hit",
            "reason": "calm evening reset",
        },
    )

    decision = decide_turn(
        "No, I want challenge and a loud social hit.",
        user="alice",
        state=state,
        vault=vault,
        persona_snapshot={"elicited": [{"title": "quiet drift", "content": "I want recovery"}]},
        occasion={"time_of_day": "evening", "day_type": "weekday", "session_length": "short"},
    )

    assert decision.intent == "ASK"
    assert "challenge" in decision.message.lower() or "loud social hit" in decision.message.lower()
    assert any((tmp_path / "vault" / "persona").glob("*.md"))


def test_model_context_only_sends_recent_turns_and_current_occasion():
    state = ConversationState(
        user="alice",
        last_turns=[
            {"intent": "ASK", "user": "one"},
            {"intent": "ACKNOWLEDGE", "user": "two"},
            {"intent": "OFFER", "user": "three"},
            {"intent": "ASK", "user": "four"},
        ],
    )

    context = build_model_context(
        state,
        persona_snapshot={"elicited": [{"title": "quiet drift", "content": "I like calm music"}]},
        occasion={"time_of_day": "evening", "day_type": "weekday", "session_length": "short"},
        max_turns=2,
    )

    assert context["occasion"]["time_of_day"] == "evening"
    assert len(context["recent_turns"]) == 2
    assert context["recent_turns"][-1]["user"] == "four"
