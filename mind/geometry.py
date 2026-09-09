from __future__ import annotations

import re


def contradiction_found(left: str, right: str) -> bool:
    left_norm = _normalize(left)
    right_norm = _normalize(right)
    if not left_norm or not right_norm:
        return False

    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    shared = left_tokens & right_tokens
    if len(shared) < 3:
        return False

    left_pos = {word for word in left_norm.split() if word in {"hopeful", "renewal", "escape", "growth", "light", "freedom", "truth"}}
    right_pos = {word for word in right_norm.split() if word in {"despairing", "decay", "entrapment", "darkness", "failure", "doom", "loss"}}
    if left_pos and right_pos:
        return True

    return False


def finish_model_allows_fit(verdicts: int) -> bool:
    return verdicts >= 60


def _normalize(text: str) -> str:
    lowered = text.lower()
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered
