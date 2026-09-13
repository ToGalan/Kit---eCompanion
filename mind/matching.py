from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from mind.freshness import availability_ok, verify_candidate

# Popular titles naturally receive outsized attention; this penalty counteracts measured
# attention concentration so a long-tail title can still outrank a blockbuster when the
# actual function fit is materially stronger.
POPULARITY_ATTENTION_PENALTY = 0.35

_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "for",
    "with",
    "into",
    "from",
    "that",
    "this",
    "what",
    "when",
    "where",
    "why",
    "how",
    "it",
    "of",
    "to",
    "in",
    "on",
    "at",
    "by",
    "is",
    "are",
    "be",
    "as",
    "if",
    "but",
    "not",
    "use",
    "uses",
    "used",
    "help",
    "helps",
    "helping",
    "media",
    "content",
    "user",
    "people",
    "person",
    "they",
    "them",
    "their",
    "after",
    "before",
    "during",
    "through",
    "just",
    "more",
    "less",
    "about",
    "over",
    "out",
    "up",
    "down",
    "all",
    "most",
    "some",
    "any",
    "few",
    "much",
    "very",
    "than",
    "when",
    "needs",
    "need",
    "want",
    "wants",
    "like",
    "likes",
    "love",
    "loves",
    "returning",
    "return",
}

_FUNCTION_SYNONYMS = {
    "calm": {"calm", "quiet", "low", "low-stimulation", "restful", "soothing", "serene", "gentle", "peaceful", "recover", "recovery", "rest", "reset", "wind", "down", "decompress", "decompression", "recharge"},
    "focus": {"focus", "concentration", "attention", "immersive", "absorbed", "engrossing", "mindful"},
    "social": {"social", "community", "friends", "shared", "group", "collab", "co-op", "talk", "conversation"},
    "challenge": {"challenge", "competition", "skill", "mastery", "puzzle", "difficulty", "win", "hard"},
    "story": {"story", "narrative", "plot", "character", "worldbuilding", "arc"},
    "discovery": {"discover", "explore", "curiosity", "find", "surprise", "new"},
    "energy": {"energy", "high-energy", "exciting", "intense", "buzz", "thrill", "adrenaline"},
    "comfort": {"cozy", "comfort", "safe", "comforting", "warm", "nostalgia"},
}


@dataclass(frozen=True)
class Match:
    title: str
    function_served: str
    evidence: list[str]
    confidence: float
    wrong_if: str


def _normalize_text(value: Any) -> str:
    text = " ".join(str(part) for part in [value] if part is not None)
    return re.sub(r"\s+", " ", text).strip()


def _tokenize(value: Any) -> list[str]:
    text = _normalize_text(value).lower()
    tokens = re.findall(r"[a-z0-9]+", text)
    return [token for token in tokens if token and token not in _STOPWORDS]


def _expand_tokens(value: Any) -> set[str]:
    tokens = set(_tokenize(value))
    expanded: set[str] = set(tokens)
    for token in list(tokens):
        for canonical, synonyms in _FUNCTION_SYNONYMS.items():
            if token in synonyms or canonical == token:
                expanded.update(synonyms)
                expanded.add(canonical)
    return expanded


def _token_overlap(left: Any, right: Any) -> float:
    left_tokens = _expand_tokens(left)
    right_tokens = _expand_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = left_tokens & right_tokens
    if not overlap:
        return 0.0
    return len(overlap) / max(len(left_tokens | right_tokens), 1)


def _candidate_functions(candidate: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("functions", "function", "function_served", "serves", "why_it_fits"):
        raw = candidate.get(key)
        if isinstance(raw, str):
            value = raw.strip()
            if value:
                values.append(value)
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, str) and item.strip():
                    values.append(item.strip())
    if not values:
        values = [str(candidate.get("title", "")).strip()]
    return values


def _persona_signals(persona_snapshot: dict[str, Any]) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    for fact in persona_snapshot.get("elicited", []) or []:
        title = str(fact.get("title", "")).strip()
        content = str(fact.get("content", "")).strip()
        if title or content:
            evidence.append({"title": title, "content": content})

    for hypothesis in persona_snapshot.get("active_hypotheses", []) or []:
        title = str(hypothesis.get("title", "")).strip()
        function = str(hypothesis.get("function", "")).strip()
        content = str(hypothesis.get("content", "")).strip()
        if function or title or content:
            evidence.append({"title": title, "content": function or content})
    return evidence


def _combine_overlaps(overlaps: list[float]) -> float:
    """Combine per-signal overlaps into a fit score in [0, 1].

    Summing them made the score unbounded, and an unbounded base is what made the
    popularity penalty meaningless: on a persona with several facts the sum passed 1.0
    on its own, every candidate clamped to the ceiling, and subtracting 0.35 changed
    nothing. Combined as a noisy-OR, more corroborating signals still raise the score --
    that is real evidence -- but they approach 1.0 rather than running past it, so the
    penalty stays material at every persona size.
    """
    combined = 0.0
    for overlap in overlaps:
        bounded = max(0.0, min(1.0, overlap))
        combined = combined + bounded - (combined * bounded)
    return combined


def _score_candidate(persona_signals: list[dict[str, str]], candidate: dict[str, Any]) -> tuple[str, float, list[str]]:
    candidate_functions = _candidate_functions(candidate)
    best_function = candidate_functions[0]
    best_score = -1.0
    evidence: list[str] = []

    for function_name in candidate_functions:
        overlaps: list[float] = []
        matched_evidence: list[str] = []
        for signal in persona_signals:
            signal_text = f"{signal.get('title', '')} {signal.get('content', '')}".strip()
            if not signal_text:
                continue
            score = _token_overlap(signal_text, function_name)
            if score > 0.0:
                overlaps.append(score)
                matched_evidence.append(signal_text)
        function_score = _combine_overlaps(overlaps)
        if function_score > best_score:
            best_score = function_score
            best_function = function_name
            evidence = matched_evidence[:3]

    if not evidence and persona_signals:
        evidence = [f"{signal.get('title', '')} {signal.get('content', '')}".strip() for signal in persona_signals[:3]]

    return best_function, max(0.0, best_score), evidence


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def match(
    persona_snapshot: dict[str, Any],
    candidates: Iterable[dict[str, Any]] | None,
    k: int = 3,
    *,
    user_services: dict[str, Any] | None = None,
    region: str | None = None,
    require_verification: bool = True,
    now: datetime | None = None,
) -> list[Match]:
    """Return the strongest function-fit matches for a persona snapshot.

    The system reasons from what the persona uses media for and what a title supplies,
    not from audience co-viewing, tags, or a separate games-only matching pipeline.

    Candidates are filtered before they are scored, never after (4.4): a title the
    person cannot reach is not ranked and then caveated, it is gone. Verification (4.5)
    is on by default, so a title no source confirms cannot be surfaced. Pass
    require_verification=False only where nothing reaches a user -- the offline metric
    harness scoring synthetic catalogues is the one such caller.
    """
    if candidates is None:
        return []

    persona_signals = _persona_signals(persona_snapshot or {})
    if not persona_signals:
        return []

    ranked: list[Match] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue

        # Availability first, and always: it only rejects a candidate that says it is
        # out of region, off the person's services, delisted or unpurchasable.
        if not availability_ok(candidate, user_services=user_services, region=region):
            continue
        if require_verification and not verify_candidate(
            candidate, user_services=user_services, region=region, now=now
        ):
            continue

        title = str(candidate.get("title") or candidate.get("name") or "").strip()
        if not title:
            continue

        wrong_if = str(candidate.get("wrong_if") or candidate.get("falsifier") or "").strip()
        if not wrong_if:
            continue

        function_name, base_score, evidence = _score_candidate(persona_signals, candidate)
        if base_score <= 0.0:
            continue

        popularity = _as_float(candidate.get("popularity", 0.0), 0.0)
        # Popularity is treated as an attention concentration variable. A title near the top
        # 1% by attention needs stronger function fit than a long-tail title to be surfaced.
        confidence = max(0.0, min(0.99, base_score - (popularity * POPULARITY_ATTENTION_PENALTY)))
        if confidence <= 0.15:
            continue

        ranked.append(
            Match(
                title=title,
                function_served=function_name,
                evidence=evidence,
                confidence=round(confidence, 3),
                wrong_if=wrong_if,
            )
        )

    ranked.sort(key=lambda item: (-item.confidence, item.title.lower()))
    return ranked[: max(0, int(k))]
