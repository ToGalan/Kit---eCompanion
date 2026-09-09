from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Recommendation:
    title: str
    reason: str
    confidence: float
    wrong_if: str
    occasion: dict[str, str] | None = None
    surfaced: bool = False


@dataclass
class ReturnValue:
    occasion: dict[str, str]
    previous_confidence: float
    current_confidence: float
    delta: float
    reason: str
    evidence: list[str] = field(default_factory=list)

    @property
    def improved(self) -> bool:
        return self.delta > 0.0


@dataclass
class CreatureState:
    growth: int = 0
    engagement_score: float = 0.0

    def reward_engagement(self, *, points: float = 1.0) -> "CreatureState":
        self.engagement_score += max(0.0, float(points))
        self.growth += 1
        return self


@dataclass
class RetentionState:
    last_seen: str | None = None
    occasion: dict[str, str] | None = None
    last_confidence: float | None = None
    current_confidence: float | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "last_seen": self.last_seen,
            "occasion": self.occasion,
            "last_confidence": self.last_confidence,
            "current_confidence": self.current_confidence,
        }
        for forbidden in ("streak", "loss_penalty", "decay", "absence_penalty", "variable_ratio"):
            payload.pop(forbidden, None)
        return payload


def _coerce_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _confidence_from_persona(persona_snapshot: dict[str, Any] | None) -> float:
    snapshot = persona_snapshot or {}
    hypotheses = snapshot.get("active_hypotheses") or []
    if not hypotheses:
        elicited = snapshot.get("elicited") or []
        return 0.25 if elicited else 0.0

    confidences: list[float] = []
    for hypothesis in hypotheses:
        confidence = _coerce_float(hypothesis.get("confidence"), default=0.0)
        if confidence > 0.0:
            confidences.append(confidence)
    if confidences:
        return max(confidences)
    return 0.35


def compounding_value(
    persona_snapshot: dict[str, Any] | None,
    *,
    previous_snapshot: dict[str, Any] | None = None,
    occasion: dict[str, str] | None = None,
) -> ReturnValue:
    current = _confidence_from_persona(persona_snapshot)
    previous = _confidence_from_persona(previous_snapshot)
    delta = current - previous
    reason = (
        "The persona is more complete than it was last time, so the recommendation is more specific and less likely to be a generic fit."
    )
    evidence = []
    for fact in (persona_snapshot or {}).get("elicited", []) or []:
        if isinstance(fact, dict):
            title = str(fact.get("title") or "fact").strip()
            if title:
                evidence.append(title)
    return ReturnValue(
        occasion=occasion or {"time_of_day": "evening", "day_type": "weekday", "session_length": "short"},
        previous_confidence=previous,
        current_confidence=current,
        delta=delta,
        reason=reason,
        evidence=evidence,
    )


def earned_anticipation(
    persona_snapshot: dict[str, Any] | None,
    candidates: list[dict[str, Any]] | None,
    *,
    min_confidence: float = 0.8,
    seen_titles: set[str] | None = None,
) -> list[Recommendation]:
    seen = seen_titles or set()
    matches: list[Recommendation] = []
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        title = str(candidate.get("title") or "").strip()
        if not title or title in seen:
            continue
        confidence = _coerce_float(candidate.get("confidence"), default=0.0)
        if confidence < min_confidence:
            continue
        reason = str(candidate.get("reason") or candidate.get("why") or "This fits the persona and is not yet surfaced.").strip()
        wrong_if = str(candidate.get("wrong_if") or "the user wants something different from this fit.").strip()
        matches.append(
            Recommendation(
                title=title,
                reason=reason,
                confidence=confidence,
                wrong_if=wrong_if,
                occasion=candidate.get("occasion"),
                surfaced=False,
            )
        )
    return matches


def reentry_opening(*, days_since_contact: int, occasion: dict[str, str] | None = None) -> str:
    if days_since_contact <= 0:
        return "I’ve kept your context, and we can pick up from where it was."
    if days_since_contact < 21:
        return (
            f"It’s been {days_since_contact} days since we checked in. I’ve kept your context, and we can pick up from where it was without a catch-up burden."
        )
    return (
        f"It’s been {days_since_contact} days since we checked in. I’ve kept the context, and we can pick up from where it was. No catch-up required."
    )


def occasion_ready_for_recommendation(
    persona_snapshot: dict[str, Any] | None,
    *,
    occasion: dict[str, str] | None,
    kind: str | None = None,
) -> bool:
    if not occasion:
        return False
    if kind is not None:
        kind = kind.lower()

    if not persona_snapshot:
        return True

    hypotheses = persona_snapshot.get("active_hypotheses") or []
    for hypothesis in hypotheses:
        if not isinstance(hypothesis, dict):
            continue
        function = str(hypothesis.get("function") or "").lower()
        if kind and function and kind not in function:
            continue
        conf = _coerce_float(hypothesis.get("confidence"), default=0.0)
        if conf >= 0.6:
            return True
    return False


def build_notification(recommendation: Recommendation | dict[str, Any], *, schedule: str | None = None) -> dict[str, Any]:
    if recommendation is None:
        raise ValueError("A notification requires a specific recommendation.")
    if isinstance(recommendation, Recommendation):
        payload = recommendation
    elif isinstance(recommendation, dict):
        payload = Recommendation(
            title=str(recommendation.get("title") or "Recommendation"),
            reason=str(recommendation.get("reason") or "This is relevant."),
            confidence=_coerce_float(recommendation.get("confidence"), default=0.0),
            wrong_if=str(recommendation.get("wrong_if") or "the user wants a different fit."),
            occasion=recommendation.get("occasion"),
            surfaced=bool(recommendation.get("surfaced", False)),
        )
    else:
        raise TypeError("recommendation must be a Recommendation or mapping")

    if schedule is not None and schedule.strip():
        raise ValueError("Scheduled notifications are not allowed unless they also carry a specific recommendation object.")
    return {
        "title": payload.title,
        "reason": payload.reason,
        "confidence": payload.confidence,
        "wrong_if": payload.wrong_if,
        "recommendation": payload.title,
        "scheduled": False,
    }
