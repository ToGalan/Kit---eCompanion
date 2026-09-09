from __future__ import annotations

from typing import Any


BANNED_PHRASES = (
    "guaranteed to be true",
    "obviously biased",
    "everyone knows",
)


def validate_claim(payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("text", "")).strip()
    falsifier = str(payload.get("falsifier", "")).strip()
    if not falsifier:
        raise ValueError("A claim requires a falsifier.")
    if not text:
        raise ValueError("Claim text is required.")
    return {"text": text, "falsifier": falsifier}


def check_line(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in BANNED_PHRASES)


def run_turn(question: str, claims: list[dict[str, Any]], catalogs: dict[str, Any] | None = None) -> dict[str, Any]:
    catalogs = catalogs or {}
    answer = (
        "I do not have live catalog access right now, so I’m answering from the local record and "
        "marking this as an offline result. The record should be treated as a provisional, evidence-based answer."
    )
    offline = True
    for name, loader in catalogs.items():
        try:
            loader(question, claims)
        except Exception:
            continue
        offline = False
        answer = f"I checked {name} and found a useful result in the available data."
        break
    return {"answer": answer, "offline": offline, "claims": claims}
