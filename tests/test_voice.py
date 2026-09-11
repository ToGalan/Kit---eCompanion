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
