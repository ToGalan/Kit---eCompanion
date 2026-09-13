from __future__ import annotations

from mind.elicitation import (
    choose_target_occasion,
    confirm_inference,
    generate_session_prompt,
)


def test_choose_target_occasion_prefers_the_largest_gap():
    snapshot = {
        "elicited": [
            {"title": "prefers quiet evenings", "content": "I like calm audio at the end of the day."},
        ],
        "active_hypotheses": [
            {"occasion": {"time_of_day": "evening", "day_type": "weekday", "session_length": "short"}, "function": "regulation", "confidence": None},
            {"occasion": {"time_of_day": "evening", "day_type": "weekday", "session_length": "short"}, "function": "mastery", "confidence": None},
            {"occasion": {"time_of_day": "night", "day_type": "weekday", "session_length": "medium"}, "function": "background", "confidence": 0.8},
            {"occasion": {"time_of_day": "night", "day_type": "weekday", "session_length": "medium"}, "function": "control", "confidence": None},
        ],
    }

    occasion = choose_target_occasion(snapshot)
    assert occasion.time_of_day == "evening"
    assert occasion.day_type == "weekday"
    assert occasion.session_length == "short"


def test_generate_session_prompt_is_indirect_and_asks_about_recent_instances():
    prompt = generate_session_prompt(
        {
            "elicited": [{"title": "likes slow evenings", "content": "I often want calm audio after work."}],
            "active_hypotheses": [{"occasion": {"time_of_day": "evening", "day_type": "weekday", "session_length": "short"}, "function": "regulation", "confidence": None}],
        },
        occasion={"time_of_day": "evening", "day_type": "weekday", "session_length": "short"},
    )

    assert "last night" in prompt.lower()
    assert "what you put on" in prompt.lower()
    assert "mood" not in prompt.lower()
    assert "what do you use music for" not in prompt.lower()


def test_confirm_inference_promotes_a_confirmed_inference_and_rejects_a_deleted_one(tmp_path):
    from kit.vault import Vault

    vault = Vault(tmp_path / "vault")
    item = {
        "title": "last-night regulation",
        "content": "The user likely uses quiet albums after work to wind down.",
        "function": "regulation",
        "occasion": {"time_of_day": "evening", "day_type": "weekday", "session_length": "short"},
    }

    confirmed = confirm_inference("alice", item, vault=vault, response="confirm")
    assert confirmed["status"] == "confirmed"
    assert confirmed["written"]["layer"] == "elicited"

    rejected = confirm_inference("alice", item, vault=vault, response="reject")
    assert rejected["status"] == "rejected"
    assert rejected["deleted"] is True


def test_a_confirmed_inference_carries_a_computed_confidence(tmp_path):
    """5.10: no literal confidence in application code, here or anywhere."""
    from pathlib import Path

    from kit.vault import Vault, parse_frontmatter
    from mind.memory import confidence_from_evidence

    vault = Vault(tmp_path / "vault")
    item = {
        "title": "last-night regulation",
        "content": "The user likely uses quiet albums after work to wind down.",
        "function": "regulation",
        "occasion": {"time_of_day": "evening", "day_type": "weekday", "session_length": "short"},
    }

    confirmed = confirm_inference("alice", item, vault=vault, response="confirm")
    written = Path(confirmed["written"]["path"]).read_text(encoding="utf-8")
    meta = parse_frontmatter(written)

    # Kit's inference, plus the person agreeing when it was put to them (5.7).
    assert float(meta["confidence"]) == confidence_from_evidence(2, layer="elicited")
    assert meta["layer"] == "elicited"


def test_confirming_an_inference_leaves_the_promoted_fact_on_disk(tmp_path):
    """5.7: confirmed is promoted to elicited, not promoted and then deleted."""
    from pathlib import Path

    from kit.vault import Vault, parse_frontmatter

    vault = Vault(tmp_path / "vault")
    item = {
        "title": "last-night regulation",
        "content": "The user likely uses quiet albums after work to wind down.",
        "function": "regulation",
    }

    confirmed = confirm_inference("alice", item, vault=vault, response="confirm")
    promoted = Path(confirmed["written"]["path"])

    assert promoted.exists()
    assert parse_frontmatter(promoted.read_text(encoding="utf-8"))["layer"] == "elicited"
    # Rejection still removes the note entirely (5.7).
    rejected = confirm_inference("alice", item, vault=vault, response="reject")
    assert rejected["deleted"] is True
    assert not promoted.exists()
