from mind.voice import recommendation_copy, validate_voice_copy


def test_voice_copy_rejects_banned_patterns_from_voice_file():
    banned = validate_voice_copy(
        "I remember you love RPGs and I know exactly what you want.",
    )
    assert banned


def test_voice_rejects_false_link_and_cutoff_disclaimers():
    for text in [
        "I can't open links.",
        "No browsing access here.",
        "Training cutoff means I can't check recent releases.",
        "My knowledge cutoff is the issue.",
        "I cannot look things up.",
    ]:
        assert validate_voice_copy(text)


def test_voice_rejects_methodology_preamble_and_waiting_opening():
    assert validate_voice_copy("I'm Kit. Quick note on how I work: I reason from evidence and tell you plainly when I'm guessing.")
    assert validate_voice_copy("I'm Kit. So what's on your mind?")
    assert validate_voice_copy("I'm Kit. Could be a decision, a claim, or just something you're trying to understand.")


def test_voice_rejects_overlong_first_message():
    text = "I'm Kit. Quick note on how I work, so you know what you're getting before we talk about games, film, music, or anything else on your mind."
    assert validate_voice_copy(text)


def test_recommendation_copy_surfaces_reason_and_wrong_if_without_violating_voice():
    copy = recommendation_copy(
        "Quiet Drift",
        why="it is short, low-stimulation, and finishes in an evening",
        wrong_if="you want a challenge loop or a loud social hit",
        memory="You bounced off the last three long RPGs, so this is shorter and more finishable.",
    )

    assert "because" in copy.lower()
    assert "wrong if" in copy.lower()
    assert "I know exactly" not in copy.lower()
    assert "mood" not in copy.lower()
    assert validate_voice_copy(copy) == []


def test_honest_uncertainty_is_allowed_because_clause_8_2_requires_it():
    """An honest empty is correct output, so the phrasing it needs cannot be banned."""
    copy = "I don't know yet what fits your Sunday mornings. What did you put on last Sunday?"

    assert validate_voice_copy(copy) == []


def test_the_word_mood_is_allowed_but_inferring_a_mood_is_not():
    # Clause 5.5 bans deriving a mood from behaviour, not saying the word.
    assert validate_voice_copy("Mood Indigo runs about 90 minutes and stays gentle.") == []

    assert validate_voice_copy(
        "From your playtime, you are feeling low tonight, so here is something soft."
    ) == ["you are feeling"]
    assert validate_voice_copy(
        "Your mood looks flat this week based on what you have been clicking."
    ) == ["your mood"]


def test_every_banned_pattern_is_actually_reachable_from_the_voice_file():
    """A pattern absent from VOICE.md never activates, so the guard would be dead."""
    from mind.voice import VOICE_BANNED_PATTERNS, load_voice_spec

    spec = load_voice_spec().lower()
    assert [pattern for pattern in VOICE_BANNED_PATTERNS if pattern not in spec] == []


def test_menu_rule_applies_to_the_opening_not_the_whole_answer():
    """VOICE.md rule 2 is about openings; ordinary prose may say "could be X or Y"."""
    assert validate_voice_copy("Could be a decision or just thinking out loud.") == [
        "menu-shaped opening"
    ]

    body = (
        "Start with Paterson. The reason it lands could be the pacing or the repetition, "
        "and either way it finishes in 118 minutes."
    )
    assert validate_voice_copy(body) == []


def test_system_prompt_carries_scope_and_the_voice_spec():
    """The spec must reach the model, not only judge it afterwards."""
    from mind.voice import kit_system_prompt, load_voice_spec

    prompt = kit_system_prompt()

    # Scope from PRINCIPLES.md 1.3 / 3.1 / 3.3.
    assert "games, film, television, anime and music" in prompt
    assert "not a therapist" in prompt
    # The whole voice spec, so the two can never drift apart.
    assert load_voice_spec().strip() in prompt


def test_a_spec_compliant_opening_passes_the_guard_it_is_checked_against():
    """VOICE.md's own reference opening must survive its own banned-pattern list."""
    opening = "I'm Kit. What's the last thing you watched or played that actually stuck with you?"

    assert validate_voice_copy(opening) == []


# --- narrowed patterns: the legitimate sentence passes, the violating one fails -------

import pytest


@pytest.mark.parametrize(
    "allowed, banned, expected",
    [
        # Clause 2.7 bans Kit claiming feelings, not the verb.
        (
            "I feel like that one might drag in the second half.",
            "I feel lonely when you go quiet.",
            "i feel",
        ),
        # Flattery is banned; describing the person plainly is not.
        (
            "You are someone who finishes things, so length is not the problem.",
            "You are so good at picking these.",
            "you are so",
        ),
        # Rule 1 bans announcing method; working something out is ordinary reasoning.
        (
            "That is how I work out what fits a short evening.",
            "Quick note on how I work before we begin.",
            "how i work",
        ),
        # Clause 7.2 bans neediness, not asking the user for something.
        (
            "I need you to paste the store link.",
            "I need you.",
            "i need you",
        ),
    ],
)
def test_narrowed_patterns_allow_the_legitimate_sentence(allowed, banned, expected):
    assert validate_voice_copy(allowed) == [], allowed
    assert expected in validate_voice_copy(banned), banned


def test_opening_only_patterns_are_anchored_to_the_start():
    # Bad as an opening.
    assert "could be a decision you're weighing" in validate_voice_copy(
        "Could be a decision you're weighing."
    )
    # The same words mid-answer are ordinary prose.
    assert validate_voice_copy(
        "The ending could be a decision you're weighing for days, and that is the point."
    ) == []


def test_capability_bans_are_still_load_bearing():
    """Clause 4.5: these must not be narrowed away, only matched precisely."""
    for claim in (
        "I have a training cutoff, so I cannot check.",
        "I can't open links.",
        "I have no browsing access.",
        "I am unable to look things up.",
    ):
        assert validate_voice_copy(claim), claim


def test_attachment_bans_are_still_load_bearing():
    """Clause 7.2: the attachment bans survive the narrowing."""
    for claim in (
        "I love you.",
        "I miss you when you do not come back.",
        "I am always here for you.",
        "I would do anything for you.",
        "I can feel your excitement.",
    ):
        assert validate_voice_copy(claim), claim


def test_retry_instruction_names_the_broken_rule():
    from mind.voice import retry_instruction

    instruction = retry_instruction(["i feel"])

    assert "claim feelings" in instruction
    assert "Rewrite" in instruction

    capability = retry_instruction(["training cutoff"])
    assert "tools" in capability
    assert "unreachable" in capability


def test_retry_instruction_does_not_repeat_a_shared_rule():
    from mind.voice import retry_instruction

    instruction = retry_instruction(["i love you", "i miss you"])

    assert instruction.count("claim feelings") == 1
