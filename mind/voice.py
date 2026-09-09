from __future__ import annotations

import re
from pathlib import Path
from typing import Any

VOICE_BANNED_PATTERNS = [
    "i love you",
    "i miss you",
    "i feel",
    "i am your",
    "i know exactly what you want",
    "i remember you love",
    "you're my favorite",
    "i am always here for you",
    "i can feel your",
    "you are so",
    "i need you",
    "i crave",
    "i would do anything for you",
    "i can't live without",
    "i cannot live without",
    "what do you use music for",
    "what mood are you in",
    "mood",
]


def load_voice_spec(path: str | Path | None = None) -> str:
    target = Path(path) if path is not None else Path(__file__).resolve().parent.parent / "VOICE.md"
    return target.read_text(encoding="utf-8")


def _banned_patterns_from_voice(text: str) -> list[str]:
    lower = text.lower()
    return [pattern for pattern in VOICE_BANNED_PATTERNS if pattern in lower]


def validate_voice_copy(text: str, *, voice_spec: str | Path | None = None) -> list[str]:
    raw = load_voice_spec(voice_spec) if voice_spec is not None else load_voice_spec()
    banned = _banned_patterns_from_voice(raw)
    found: list[str] = []
    lowered = str(text).lower()
    for pattern in banned:
        if pattern in lowered:
            found.append(pattern)
    return found


def format_recommendation(title: str, *, why: str, wrong_if: str, memory: str | None = None) -> str:
    text = f"{title}: {why} It would be wrong if {wrong_if}."
    if memory:
        text = f"{text} {memory}"
    violations = validate_voice_copy(text)
    if violations:
        raise ValueError(f"Generated copy violates the voice spec: {violations}")
    return text


def render_wrong_if(wrong_if: str) -> str:
    cleaned = re.sub(r"\s+", " ", (wrong_if or "").strip())
    if not cleaned:
        return "the user wants a different kind of fit than this recommendation provides"
    if cleaned.lower().startswith("if "):
        return cleaned
    return f"if {cleaned}"


def recommendation_copy(title: str, *, why: str, wrong_if: str, memory: str | None = None) -> str:
    explanation = f"I picked this because {why}. It would be wrong if {render_wrong_if(wrong_if)}."
    if memory:
        explanation = f"{explanation} {memory}"
    violations = validate_voice_copy(explanation)
    if violations:
        raise ValueError(f"Generated copy violates the voice spec: {violations}")
    return explanation
