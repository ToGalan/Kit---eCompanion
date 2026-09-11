from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from kit.vault import Occasion, Vault

_HIGH_CONFIDENCE = 0.8


def _normalize_occasion(raw: Any) -> Occasion | None:
    if raw is None:
        return None
    if isinstance(raw, Occasion):
        return raw
    if not isinstance(raw, dict):
        return None
    # Every field must be present. Filling a missing one in with "evening" or "short"
    # invents context the user never gave, and it reads downstream as though they did.
    time_of_day = str(raw.get("time_of_day") or "").strip()
    day_type = str(raw.get("day_type") or "").strip()
    session_length = str(raw.get("session_length") or "").strip()
    if not (time_of_day and day_type and session_length):
        return None
    label = str(raw.get("label") or "").strip() or None
    try:
        return Occasion(
            time_of_day=time_of_day,
            day_type=day_type,
            session_length=session_length,
            label=label,
        )
    except ValueError:
        return None


def _hypothesis_confidence(item: dict[str, Any]) -> float | None:
    value = item.get("confidence")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_known_hypothesis(item: dict[str, Any]) -> bool:
    conf = _hypothesis_confidence(item)
    return conf is not None and conf >= _HIGH_CONFIDENCE


def _count_untested_hypotheses(persona_snapshot: dict[str, Any] | None) -> dict[Occasion, list[dict[str, Any]]]:
    snapshot = persona_snapshot or {}
    by_occasion: dict[Occasion, list[dict[str, Any]]] = {}
    for item in snapshot.get("active_hypotheses", []) or []:
        if not isinstance(item, dict):
            continue
        if _is_known_hypothesis(item):
            continue
        occasion = _normalize_occasion(item.get("occasion"))
        if occasion is None:
            continue
        by_occasion.setdefault(occasion, []).append(item)
    return by_occasion


def choose_target_occasion(persona_snapshot: dict[str, Any] | None) -> Occasion:
    by_occasion = _count_untested_hypotheses(persona_snapshot)
    if by_occasion:
        occasion, items = max(
            by_occasion.items(),
            key=lambda pair: (len(pair[1]), pair[0].time_of_day != "evening", pair[0].day_type != "weekday"),
        )
        if items:
            return occasion

    return Occasion(
        time_of_day="evening",
        day_type="weekday",
        session_length="short",
        label="wind-down",
    )


def generate_session_prompt(persona_snapshot: dict[str, Any] | None, *, occasion: dict[str, Any] | Occasion | None = None) -> str:
    snapshot = persona_snapshot or {}
    target = _normalize_occasion(occasion) or choose_target_occasion(snapshot)
    labels = {
        "morning": "this morning",
        "afternoon": "earlier today",
        "evening": "last night",
        "night": "late last night",
    }
    when = labels.get(target.time_of_day, "recently")

    prompt = (
        "Quick check-in: when you were {when}, what you put on, and whether it did the job. "
        "If you bailed halfway through or switched away, what happened right before that?"
    ).format(when=when)

    known = [
        str(fact.get("title") or fact.get("content") or "").strip()
        for fact in snapshot.get("elicited", []) or []
        if isinstance(fact, dict) and str(fact.get("title") or fact.get("content") or "").strip()
    ]
    if known:
        prompt = f"{prompt} I’m not asking for a broad profile—just the last concrete example."
    prompt += " We can keep this to three quick exchanges, max."
    prompt = prompt.replace("what did you put on", "what you put on")
    return prompt


def _user_visible_hypothesis_text(hypothesis: dict[str, Any]) -> str:
    function = str(hypothesis.get("function") or "this media use").strip()
    title = str(hypothesis.get("title") or function).strip()
    content = str(hypothesis.get("content") or f"Kit is picking up that this likely helps with {function}.").strip()
    if content.endswith("."):
        return f"I’m noticing {title.lower()} — {content.lower()}"
    return f"I’m noticing {title.lower()} — {content.lower()}."


def _hypothesis_file(vault: Vault, title: str) -> Path | None:
    match_name = re.sub(r"\s+", " ", title).strip()
    for path in sorted((vault.path / "hypotheses").glob("*.md")):
        if path.stem.lower() == match_name.lower():
            return path
    return None


def _write_inferred_hypothesis(vault: Vault, hypothesis: dict[str, Any], user: str) -> Path:
    title = str(hypothesis.get("title") or hypothesis.get("function") or "inferred hypothesis").strip()
    content = str(hypothesis.get("content") or _user_visible_hypothesis_text(hypothesis)).strip()
    return vault.write_persona_fact(
        title=title,
        content=content,
        reason=f"{user}: inferred from behaviour",
        layer="inferred",
        source="kit inference",
        confidence=float(hypothesis.get("confidence") or 0.6),
    )


def confirm_inference(
    user: str,
    hypothesis: dict[str, Any],
    *,
    vault: Vault | None = None,
    response: str = "confirm",
    correction: str | None = None,
) -> dict[str, Any]:
    vault = vault or Vault(Path(os.environ.get("KIT_VAULT_PATH", "vault")))
    normalized = str(response or "confirm").strip().lower()
    title = str(hypothesis.get("title") or hypothesis.get("function") or "inferred hypothesis").strip()
    plain = _user_visible_hypothesis_text(hypothesis)
    inferred_path = _hypothesis_file(vault, title)
    if inferred_path is None:
        inferred_path = _write_inferred_hypothesis(vault, hypothesis, user)

    if normalized in {"reject", "delete"}:
        inferred_path.unlink(missing_ok=True)
        return {"status": "rejected", "deleted": True, "message": "I’ve dropped that inference and won’t keep it."}

    if normalized == "correct":
        correction_text = (correction or hypothesis.get("correction") or "The user corrected this inference.").strip()
        written = vault.write_persona_fact(
            title=f"Corrected: {title}",
            content=correction_text,
            reason=f"{user}: corrected inferred hypothesis",
            layer="elicited",
            source="user correction",
            confidence=0.9,
        )
        inferred_path.unlink(missing_ok=True)
        return {"status": "corrected", "written": {"path": str(written), "layer": "elicited"}, "deleted": True}

    written = vault.write_persona_fact(
        title=title,
        content=plain,
        reason=f"{user}: confirmed inference",
        layer="elicited",
        source="inference confirmation",
        confidence=0.85,
    )
    inferred_path.unlink(missing_ok=True)
    return {"status": "confirmed", "message": plain, "written": {"path": str(written), "layer": "elicited"}, "deleted": True}
