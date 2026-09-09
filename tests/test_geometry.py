import pytest

from mind.geometry import contradiction_found, finish_model_allows_fit


def test_contradiction_found_for_similar_opposite_claims():
    left = "This film is a hopeful story about escape and renewal."
    right = "This film is a despairing story about entrapment and decay."

    assert contradiction_found(left, right) is True


def test_finish_model_refuses_early_and_accepts_later():
    assert finish_model_allows_fit(12) is False
    assert finish_model_allows_fit(60) is True
