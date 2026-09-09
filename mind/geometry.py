from __future__ import annotations

from mind.embeddings import similarity


def contradiction_found(left: str, right: str) -> bool:
    if not left or not right:
        return False

    subject_overlap = similarity(left, right)
    if subject_overlap < 0.45:
        return False

    left_hopeful = similarity(left, "hopeful")
    left_despairing = similarity(left, "despairing")
    right_hopeful = similarity(right, "hopeful")
    right_despairing = similarity(right, "despairing")

    left_stance = left_hopeful - left_despairing
    right_stance = right_hopeful - right_despairing

    if abs(left_stance) < 0.05 or abs(right_stance) < 0.05:
        return False
    return left_stance * right_stance < 0.0
