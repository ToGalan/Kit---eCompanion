import pytest

from mind.geometry import contradiction_found


def test_contradiction_found_for_similar_opposite_claims():
    left = "This film is a hopeful story about escape and renewal."
    right = "This film is a despairing story about entrapment and decay."

    assert contradiction_found(left, right) is True


def test_contradiction_found_rejects_unrelated_text():
    left = "The hero learns to be kind to the city."
    right = "The weather is unusually warm today."

    assert contradiction_found(left, right) is False
