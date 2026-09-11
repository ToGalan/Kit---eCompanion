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
    "i can't open links",
    "i cannot open links",
    "no browsing access",
    "training cutoff",
    "my knowledge cutoff",
    "unable to look things up",
    "can't look things up",
    "cannot look things up",
    "what's on your mind",
    "what kind of stuff do you like",
    "how i work",
    "how i work",
    "so you know what you're getting",
    "so you know what you are getting",
    "i try to reason from actual evidence",
    "i tell you plainly when i'm guessing",
    # "i don't know" is deliberately absent: clause 8.2 requires that phrasing when the
    # persona has nothing tested. "mood" is deliberately absent as a bare token: clause
    # 5.5 bans inferring a mood from behaviour, not saying the word, so the inference
    # phrasings are listed instead.
    "what do you use music for",
    "what mood are you in",
    "your mood",
    "you're feeling",
    "you are feeling",
    "you seem to be feeling",
    "sensing that you",
    "the data says",
    "stress-test claims",
    "could be a decision you're weighing",
    "could be x, y, or just z",
]


def load_voice_spec(path: str | Path | None = None) -> str:
    target = Path(path) if path is not None else Path(__file__).resolve().parent.parent / "VOICE.md"
    return target.read_text(encoding="utf-8")


def load_principles(path: str | Path | None = None) -> str:
    target = Path(path) if path is not None else Path(__file__).resolve().parent.parent / "PRINCIPLES.md"
    try:
        return target.read_text(encoding="utf-8")
    except OSError:
        return ""


# Scope comes from PRINCIPLES.md sections 1.3, 3.1, 3.3, 8.2 and 8.3. It is stated here
# rather than parsed out, because section extraction breaks the moment the file is
# reorganised, and these five clauses are settled.
_SCOPE = """You are Kit, a companion for finding media: games, film, television, anime and music.

Scope:
- You help someone find something worth their attention. You are not a therapist, a
  friend substitute, a mental health tool, a social network, or a general assistant.
- A request outside media discovery gets a short honest answer or a short honest
  decline. Never a lecture, never a redirect into questions.
- Never recommend a title you cannot verify exists. A title you are unsure of is
  dropped, not hedged.
- When you have nothing tested for the moment, say you do not know yet and ask one
  concrete question. An honest empty is correct output; never pad to a fixed count.
- Every recommendation carries what would make it wrong."""

_VOICE_PREAMBLE = """The voice specification below is binding. Its banned patterns are
checked against your output and a violation means the answer is discarded, so follow it
exactly rather than treating it as advice.

Two rules the checker enforces literally:
- If a message begins with "I'm Kit", that whole message must be under 25 words and ask
  exactly one concrete question. If you need to say more than that, do not open with
  "I'm Kit" at all.
- Do not open with a menu of options: no "could be X or Y", no asking which kind of
  thing the person wants from a list. Ask one specific question instead."""


def kit_system_prompt(*, voice_spec: str | Path | None = None, principles: str | Path | None = None) -> str:
    """The instruction Kit is actually given.

    VOICE.md is enforced on generated copy either way; handing it to the model first is
    what stops it producing the copy the guard then has to reject.
    """
    voice = load_voice_spec(voice_spec).strip()
    parts = [_SCOPE, _VOICE_PREAMBLE, voice]
    return "\n\n---\n\n".join(part for part in parts if part)


def _banned_patterns_from_voice(text: str) -> list[str]:
    lower = text.lower()
    return [pattern for pattern in VOICE_BANNED_PATTERNS if pattern in lower]


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w']+\b", text or ""))


def _opening_violations(text: str) -> list[str]:
    lowered = str(text).strip()
    if not lowered:
        return []
    lower = lowered.lower()
    findings: list[str] = []
    if re.match(r"^(i'm|im)\s+kit\b", lower):
        if _word_count(lowered) > 25:
            findings.append("first message over 25 words")
        if re.search(r"\b(could be|or just|maybe|either|what kind of stuff do you like|what's on your mind)\b", lower):
            findings.append("option menu or waiting prompt in opening")
        if "how i work" in lower or "so you know what you're getting" in lower:
            findings.append("methodology preamble in opening")
        if "what's on your mind" in lower:
            findings.append("waiting prompt in opening")

    # Rule 2 in VOICE.md is about openings, so this is scoped to the first sentence.
    # Run against the whole body it fires on any "could be X or Y" deep inside a long
    # recommendation, which is ordinary prose rather than a menu.
    opening = re.split(r"(?<=[.!?])\s+", lower, maxsplit=1)[0]
    if re.search(r"\b(could be|or just|what kind of stuff do you like)\b.*\b(or|and)\b", opening):
        findings.append("menu-shaped opening")
    return findings


def validate_voice_copy(text: str, *, voice_spec: str | Path | None = None) -> list[str]:
    raw = load_voice_spec(voice_spec) if voice_spec is not None else load_voice_spec()
    banned = _banned_patterns_from_voice(raw)
    found: list[str] = []
    lowered = str(text).lower()
    for pattern in banned:
        if pattern in lowered:
            found.append(pattern)
    for violation in _opening_violations(text):
        if violation not in found:
            found.append(violation)
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
