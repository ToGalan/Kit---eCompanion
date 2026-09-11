from __future__ import annotations

from pathlib import Path

from kit.vault import Vault
from mind.conversation import ConversationState, build_model_context, decide_turn
from mind.tools import DEFAULT_TOOLS


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


def test_url_resolution_uses_live_tracks_in_answer(monkeypatch):
    def fake_resolve_url(**kwargs):
        return {
            "tool": "resolve_url",
            "source": "resolve_url",
            "ok": True,
            "fetched_at": "2026-01-01T00:00:00+00:00",
            "data": {
                "kind": "playlist",
                "title": "Spotify playlist",
                "tracks": ["Heatwaves - Glass Animals", "Dreams - Fleetwood Mac"],
            },
        }

    monkeypatch.setitem(DEFAULT_TOOLS, "resolve_url", type("T", (), {"run": staticmethod(fake_resolve_url)})())
    decision = decide_turn(
        "https://open.spotify.com/playlist/abc123",
        user="alice",
        persona_snapshot={"elicited": [{"title": "quiet drift", "content": "I like calm music"}]},
        occasion={"time_of_day": "evening", "day_type": "weekday", "session_length": "short"},
    )

    assert decision.intent == "ANSWER"
    assert "Heatwaves" in decision.message
    assert "Dreams" in decision.message
    assert "training cutoff" not in decision.message.lower()


def test_title_question_with_failed_tools_says_could_not_reach_and_not_cutoff(monkeypatch):
    for name in ["steam_catalog", "igdb_lookup", "tmdb_lookup", "anilist_lookup", "musicbrainz_lookup", "web_search"]:
        monkeypatch.setitem(
            DEFAULT_TOOLS,
            name,
            type("T", (), {"run": staticmethod(lambda **kwargs: {"tool": name, "ok": False, "error": "timed out", "data": {}})})(),
        )

    decision = decide_turn(
        "What is the release date of The Matrix?",
        user="alice",
        persona_snapshot={"elicited": [{"title": "quiet drift", "content": "I like calm music"}]},
        occasion={"time_of_day": "evening", "day_type": "weekday", "session_length": "short"},
    )

    assert decision.intent == "ANSWER"
    assert "could not reach that source right now" in decision.message.lower()
    assert "training cutoff" not in decision.message.lower()
    assert "remember" not in decision.message.lower()


def test_model_context_reports_an_unknown_occasion_as_unknown():
    from mind.conversation import ConversationState, build_model_context

    context = build_model_context(ConversationState(user="brian"))

    assert context["occasion"] is None
