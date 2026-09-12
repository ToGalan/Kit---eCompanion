from __future__ import annotations

import pytest

from kit.vault import Occasion, Vault, parse_evidence, parse_frontmatter
from mind import memory


@pytest.fixture()
def vault(tmp_path) -> Vault:
    return Vault(tmp_path / "vault")


@pytest.fixture()
def occasion() -> Occasion:
    return Occasion(time_of_day="evening", day_type="weekday", session_length="short")


def _persona_notes(vault: Vault) -> list[str]:
    return sorted(path.stem for path in (vault.path / "persona").glob("*.md"))


def test_confidence_rises_with_accumulated_evidence_and_never_reaches_certainty():
    first = memory.confidence_from_evidence(1, layer="elicited")
    second = memory.confidence_from_evidence(2, layer="elicited")
    many = memory.confidence_from_evidence(50, layer="elicited")

    assert 0.0 < first < second < 1.0
    assert many == memory.CONFIDENCE_CEILING
    # Nothing observed is nothing known, not a weak belief.
    assert memory.confidence_from_evidence(0, layer="observed") == 0.0


def test_confidence_is_weaker_for_what_kit_watched_than_for_what_the_user_said():
    assert memory.confidence_from_evidence(1, layer="observed") < memory.confidence_from_evidence(1, layer="elicited")
    assert memory.confidence_from_evidence(1, layer="inferred") < memory.confidence_from_evidence(1, layer="observed")


def test_confidence_refuses_an_unknown_layer():
    with pytest.raises(ValueError, match="layer"):
        memory.confidence_from_evidence(3, layer="guessed")


def test_record_exchange_writes_both_turns_to_one_transcript(vault, occasion):
    result = memory.record_exchange(
        vault,
        "ana",
        session_id="ana-2026-09-12",
        user_text="I usually play something short on a weeknight.",
        kit_text="Then a 25-minute loop is the shape to look for.",
        occasion=occasion,
    )

    assert result["recorded"] is True
    turns = vault.conversation_turns("ana-2026-09-12")
    assert [turn["speaker"] for turn in turns] == ["user", "kit"]
    assert turns[0]["text"] == "I usually play something short on a weeknight."
    assert turns[1]["text"] == "Then a 25-minute loop is the shape to look for."

    note = vault.get_note("ana-2026-09-12", "conversations")
    meta = parse_frontmatter(note["content"])
    assert meta["user"] == "ana"
    # The occasion travels with the transcript, marked as the derived context it is.
    assert meta["occasion_time_of_day"] == "evening"


def test_a_later_exchange_appends_rather_than_replacing(vault):
    for index in range(2):
        memory.record_exchange(
            vault,
            "ana",
            session_id="ana-2026-09-12",
            user_text=f"question {index}",
            kit_text=f"answer {index}",
        )

    turns = vault.conversation_turns("ana-2026-09-12")
    assert [turn["text"] for turn in turns] == ["question 0", "answer 0", "question 1", "answer 1"]


def test_multi_line_input_stays_one_turn(vault):
    memory.record_exchange(
        vault,
        "ana",
        session_id="s1",
        user_text="I like slow films.\nEspecially on a Sunday.",
        kit_text="Noted.",
    )

    turns = vault.conversation_turns("s1")
    assert len(turns) == 2
    assert turns[0]["text"] == "I like slow films. Especially on a Sunday."


def test_a_durable_statement_becomes_an_elicited_persona_fact(vault):
    result = memory.record_exchange(
        vault,
        "ana",
        session_id="s1",
        user_text="I like slow films that leave room to think.",
        kit_text="Understood.",
    )

    assert len(result["facts"]) == 1
    fact = result["facts"][0]
    assert fact["layer"] == "elicited"
    assert fact["confidence"] == memory.confidence_from_evidence(1, layer="elicited")

    note = vault.get_note(fact["title"], "persona")
    meta = parse_frontmatter(note["content"])
    assert meta["layer"] == "elicited"
    assert meta["source"] == "chat:s1"


def test_passing_conversation_is_not_filed_as_persona(vault):
    result = memory.record_exchange(
        vault,
        "ana",
        session_id="s1",
        user_text="What time does it come out?",
        kit_text="Friday.",
    )

    assert result["recorded"] is True
    assert result["facts"] == []
    assert _persona_notes(vault) == []


def test_repeating_a_statement_adds_evidence_and_raises_confidence(vault):
    statement = "I like slow films that leave room to think."
    first = memory.record_statement(vault, "ana", statement, session_id="s1")
    second = memory.record_statement(vault, "ana", statement, session_id="s2")

    assert second["confidence"] > first["confidence"]
    assert second["evidence"] == ["s1", "s2"]
    # Corroboration updates the fact rather than duplicating it.
    assert len(_persona_notes(vault)) == 1

    note = vault.get_note(second["title"], "persona")
    assert parse_evidence(parse_frontmatter(note["content"]).get("evidence")) == ["s1", "s2"]


def test_saying_it_again_in_the_same_session_is_not_two_pieces_of_evidence(vault):
    statement = "I like slow films that leave room to think."
    memory.record_statement(vault, "ana", statement, session_id="s1")
    repeated = memory.record_statement(vault, "ana", statement, session_id="s1")

    assert repeated["evidence"] == ["s1"]
    assert repeated["confidence"] == memory.confidence_from_evidence(1, layer="elicited")


def test_distress_is_never_filed(vault):
    result = memory.record_exchange(
        vault,
        "ana",
        session_id="s1",
        user_text="I want to die and I like watching sad films about it.",
        kit_text="That sounds heavy, and I am not the right thing for it.",
    )

    assert result["recorded"] is False
    assert result["reason"] == "distress"
    assert result["facts"] == []
    # Not as a persona fact, and not as transcript either: the transcript is storage too.
    assert _persona_notes(vault) == []
    assert vault.conversation_turns("s1") == []


def test_a_protected_category_statement_is_never_filed(vault):
    result = memory.record_exchange(
        vault,
        "ana",
        session_id="s1",
        user_text="I am autistic and I like predictable shows.",
        kit_text="Predictable structure it is.",
    )

    assert result["recorded"] is False
    assert result["reason"] == "protected"
    assert _persona_notes(vault) == []
    assert vault.conversation_turns("s1") == []


def test_observed_facts_are_counts_of_what_happened(vault):
    history = {
        "events": [
            {"domain": "bandcamp.com", "signal": "save"},
            {"domain": "bandcamp.com", "signal": "save"},
            {"domain": "bandcamp.com", "signal": "bounce"},
            {"domain": "netflix.com", "signal": "bounce"},
        ]
    }

    written = memory.compile_observed_facts(vault, "ana", history)

    # One visit is an anecdote, so netflix.com does not become a fact yet.
    assert [item["title"] for item in written] == ["Signal: bandcamp.com"]
    assert written[0]["layer"] == "observed"
    assert written[0]["confidence"] == memory.confidence_from_evidence(3, layer="observed")

    note = vault.get_note("Signal: bandcamp.com", "persona")
    body = note["content"]
    assert "3 visits" in body and "2 saved" in body
    # 5.5: what happened, never why. No mood, energy or state is derived from the counts.
    assert not any(word in body.lower() for word in ["mood", "feeling", "energy", "stressed", "relaxed"])


def test_unchanged_signal_is_not_rewritten(vault):
    history = {"events": [{"domain": "bandcamp.com", "signal": "save"}] * 3}

    assert memory.compile_observed_facts(vault, "ana", history) != []
    # The same tally again records nothing, so the note history stays meaningful.
    assert memory.compile_observed_facts(vault, "ana", history) == []
    assert len(vault.history("Signal: bandcamp.com", "persona")) == 1


def test_one_persons_facts_do_not_reach_another(vault):
    memory.record_statement(vault, "ana", "I like slow films that leave room to think.", session_id="ana-1")
    memory.record_statement(vault, "bo", "I like loud arcade games.", session_id="bo-1")

    ana = memory.recall(vault, "ana", session_id="ana-1")
    assert [fact["content"] for fact in ana["facts"]] == ["I like slow films that leave room to think."]


def test_a_fact_written_before_facts_had_an_owner_stays_visible(vault):
    vault.write_persona_fact(
        title="Legacy fact",
        content="Recorded before persona facts carried a user.",
        reason="migration",
        layer="elicited",
        source="touchpoint",
        confidence=0.5,
    )

    recalled = memory.recall(vault, "ana", session_id="ana-1")

    assert [fact["title"] for fact in recalled["facts"]] == ["Legacy fact"]


def test_memory_writes_no_hypothesis_without_an_occasion(vault, occasion):
    memory.record_exchange(
        vault,
        "ana",
        session_id="s1",
        user_text="I usually play roguelikes to wind down after work.",
        kit_text="Short runs, then.",
        occasion=occasion,
    )
    memory.compile_observed_facts(vault, "ana", {"events": [{"domain": "steamcommunity.com", "signal": "save"}] * 4})

    # A hypothesis needs a function drawn from an episode (5.4) and an occasion (5.2).
    # Counting signal supplies neither, so memory files facts and proposes nothing.
    assert list((vault.path / "hypotheses").glob("*.md")) == []


def test_every_memory_write_carries_a_reason(vault):
    memory.record_exchange(
        vault,
        "ana",
        session_id="s1",
        user_text="I like slow films that leave room to think.",
        kit_text="Noted.",
    )

    transcript_history = vault.history("s1", "conversations")
    assert transcript_history and transcript_history[0]["reason"] == "ana: chat exchange"
    fact_title = _persona_notes(vault)[0]
    assert vault.history(fact_title, "persona")[0]["reason"] == "ana: stated in chat"


def test_recall_reaches_back_into_earlier_sessions(vault):
    memory.record_exchange(vault, "ana", session_id="ana-monday", user_text="monday question", kit_text="monday answer")
    memory.record_exchange(vault, "ana", session_id="ana-tuesday", user_text="tuesday question", kit_text="tuesday answer")

    recalled = memory.recall(vault, "ana", session_id="ana-tuesday")
    texts = [turn["text"] for turn in recalled["turns"]]

    assert texts == ["monday question", "monday answer", "tuesday question", "tuesday answer"]


def test_recall_keeps_another_users_sessions_out(vault):
    memory.record_exchange(vault, "ana", session_id="ana-monday", user_text="ana question", kit_text="ana answer")
    memory.record_exchange(vault, "bo", session_id="bo-monday", user_text="bo question", kit_text="bo answer")

    recalled = memory.recall(vault, "ana", session_id="ana-today")

    assert [turn["text"] for turn in recalled["turns"]] == ["ana question", "ana answer"]


def test_recalled_turns_become_real_messages(vault):
    memory.record_exchange(vault, "ana", session_id="s1", user_text="what did I say?", kit_text="you said this")

    messages = memory.conversation_messages(memory.recall(vault, "ana", session_id="s1"))

    assert messages == [
        {"role": "user", "content": "what did I say?"},
        {"role": "assistant", "content": "you said this"},
    ]


def test_memory_block_separates_nothing_recorded_from_nothing_tested(vault, occasion):
    empty = memory.memory_block(memory.recall(vault, "ana", session_id="s1", occasion=occasion))
    assert "Nothing recorded about this person yet" in empty

    memory.record_statement(vault, "ana", "I like slow films that leave room to think.", session_id="s1")
    populated = memory.memory_block(memory.recall(vault, "ana", session_id="s1", occasion=occasion))

    assert "Nothing recorded about this person yet" not in populated
    assert "Nothing tested for this occasion yet" in populated


def test_memory_block_carries_the_layer_of_every_fact(vault, occasion):
    memory.record_statement(vault, "ana", "I like slow films that leave room to think.", session_id="s1")
    memory.compile_observed_facts(vault, "ana", {"events": [{"domain": "bandcamp.com", "signal": "save"}] * 3})

    block = memory.memory_block(memory.recall(vault, "ana", session_id="s1", occasion=occasion))

    assert "[elicited]" in block
    assert "[observed]" in block
    # 2.6: memory does work, it is not performed.
    assert "do not announce that you remember" in block


def test_memory_block_says_when_the_occasion_is_unknown(vault):
    block = memory.memory_block(memory.recall(vault, "ana", session_id="s1", occasion=None))

    assert "Occasion now: not known." in block
