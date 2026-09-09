from mind.voice import recommendation_copy, validate_voice_copy


def test_voice_copy_rejects_banned_patterns_from_voice_file():
    banned = validate_voice_copy(
        "I remember you love RPGs and I know exactly what you want.",
    )
    assert banned


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
