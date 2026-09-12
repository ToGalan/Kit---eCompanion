from __future__ import annotations

import json
import logging
from typing import Any

from mind.ai import Opus5Gateway
from mind.tools import DEFAULT_TOOLS

logger = logging.getLogger(__name__)


BANNED_PHRASES = (
    "guaranteed to be true",
    "obviously biased",
    "everyone knows",
)


def validate_claim(payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("text", "")).strip()
    falsifier = str(payload.get("falsifier", "")).strip()
    if not text:
        raise ValueError("Claim text is required.")
    if not falsifier:
        raise ValueError("A claim requires a falsifier.")
    return {"text": text, "falsifier": falsifier}


def check_line(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in BANNED_PHRASES)


def _tool_schema_list(tools: dict[str, Any]) -> list[dict[str, Any]]:
    schemas: list[dict[str, Any]] = []
    for name, tool in tools.items():
        if hasattr(tool, "name") and hasattr(tool, "description") and hasattr(tool, "input_schema"):
            schemas.append(
                {
                    "name": getattr(tool, "name") or name,
                    "description": getattr(tool, "description") or f"Tool for {name}",
                    "input_schema": getattr(tool, "input_schema") or {"type": "object", "properties": {}, "additionalProperties": True},
                }
            )
        else:
            schemas.append(
                {
                    "name": name,
                    "description": f"Legacy catalog function for {name}",
                    "input_schema": {"type": "object", "properties": {}, "additionalProperties": True},
                }
            )
    return schemas


def _format_tool_results(results: list[dict[str, Any]]) -> str:
    pieces: list[str] = []
    for result in results:
        tool_name = result.get("tool", "unknown")
        if not result.get("ok"):
            pieces.append(f"{tool_name}: unavailable ({result.get('error', 'no detail')}).")
            continue
        source = result.get("source", tool_name)
        fetched_at = result.get("fetched_at")
        if fetched_at:
            pieces.append(f"{tool_name}: live evidence from {source}, fetched {fetched_at}.")
        else:
            # A fact without both a source and a fetch timestamp has no provenance and
            # cannot be presented as evidence.
            pieces.append(f"{tool_name}: {source} answered without a fetch timestamp, so it is not usable as evidence.")
    return " ".join(pieces)


def _normalize_tool_result(name: str, result: Any) -> dict[str, Any]:
    """Coerce whatever a tool returned into the result shape the loop expects."""
    if isinstance(result, dict):
        payload = dict(result)
        payload.setdefault("tool", name)
        payload.setdefault("source", name)
        payload.setdefault("ok", False)
        payload.setdefault("fetched_at", None)
        return payload
    return {
        "tool": name,
        "source": name,
        "ok": False,
        "fetched_at": None,
        "error": f"tool returned {type(result).__name__}, expected a mapping",
    }


def _call_tool(tool: Any, name: str, question: str, claims: list[dict[str, Any]], arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        if hasattr(tool, "run"):
            result = tool.run(**arguments)
        else:
            result = tool(question, claims, **arguments)
    except Exception as exc:
        # A failing tool removes its domain from the request; it does not fail the request.
        return {"tool": name, "source": name, "ok": False, "fetched_at": None, "error": str(exc)}
    return _normalize_tool_result(name, result)


def _offline_answer(vault_evidence: list[dict[str, Any]]) -> str:
    """The honest answer when no live source could be reached.

    An empty vault says so plainly rather than filling the gap. A populated one names
    what it rests on, with the layer attached, so a stated fact is not passed off as an
    inference or the reverse.
    """
    if not vault_evidence:
        return (
            "I could not reach any configured catalog or web source, so I’m marking this result as offline. "
            "The record remains provisional and evidence-first."
        )
    named = ", ".join(
        f"{note.get('title') or 'untitled note'} ({note.get('layer') or 'layer not recorded'})"
        for note in vault_evidence
    )
    return (
        "I could not reach any configured catalog or web source, so this is offline and rests only on the "
        f"vault record: {named}. Treat it as provisional until a live source confirms it."
    )


def _vault_evidence(brain: Any, question: str, *, seed_title: str | None, limit: int) -> list[dict[str, Any]]:
    """Pull grounding notes out of the vault map, without letting it break the turn."""
    if brain is None:
        return []
    try:
        related = brain.related(question, limit=limit, seed_title=seed_title)
    except Exception:
        logger.warning("vault mapping unavailable for this turn", exc_info=True)
        return []
    evidence: list[dict[str, Any]] = []
    for note in related or []:
        if not isinstance(note, dict):
            continue
        evidence.append(
            {
                "title": note.get("title"),
                "kind": note.get("kind"),
                # The layer travels with the fact. None means the note never declared
                # one, which is different from the user having said it.
                "layer": note.get("layer"),
                "source": note.get("source"),
                "relation": note.get("relation"),
                "body": str(note.get("body") or "")[:600],
            }
        )
    return evidence


def run_turn(
    question: str,
    claims: list[dict[str, Any]],
    catalogs: dict[str, Any] | None = None,
    *,
    brain: Any | None = None,
    seed_title: str | None = None,
    vault_limit: int = 5,
) -> dict[str, Any]:
    tool_map: dict[str, Any] = catalogs if catalogs else DEFAULT_TOOLS
    gateway = Opus5Gateway()
    vault_evidence = _vault_evidence(brain, question, seed_title=seed_title, limit=vault_limit)
    # Seeded from the vault, so a turn that never reaches the model still answers from
    # what the map holds instead of asserting there is nothing.
    answer = _offline_answer(vault_evidence)
    tool_results: list[dict[str, Any]] = []
    offline = True
    attempted: set[tuple[str, str]] = set()

    for _ in range(6):
        payload: dict[str, Any] = {
            "question": question,
            "claims": claims,
            "tools": _tool_schema_list(tool_map),
            # Results have to travel back into the payload, or every pass sees the same
            # input and asks for the same tools again.
            "tool_results": tool_results,
            # What the vault already holds about this question, each note carrying its
            # layer and source so the model can weigh stated facts against inferred ones.
            "vault_evidence": vault_evidence,
        }
        try:
            raw = gateway.generate_structured(
                # default=str: tool results are arbitrary payloads from external sources and
                # must not be able to crash the turn on an unserializable value.
                json.dumps(payload, sort_keys=True, default=str),
                {"type": "object", "properties": {"tool_calls": {"type": "array"}, "final_answer": {"type": "string"}}, "additionalProperties": True},
            )
        except Exception:
            # Degrade to the offline answer, but leave a trace: a missing API key and a
            # malformed model response should not look identical from the outside.
            logger.warning("structured generation failed; falling back to the offline answer", exc_info=True)
            raw = {"final_answer": answer}

        tool_calls = raw.get("tool_calls") if isinstance(raw, dict) else None
        if isinstance(tool_calls, list) and tool_calls:
            progressed = False
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                name = str(call.get("name", "")).strip()
                if not name:
                    continue
                tool = tool_map.get(name)
                if tool is None:
                    continue
                arguments = call.get("arguments") or {}
                if not isinstance(arguments, dict):
                    arguments = {}
                # Never run the same tool with the same arguments twice; a model that keeps
                # asking for a failing tool would otherwise burn every turn on it.
                signature = (name, json.dumps(arguments, sort_keys=True, default=str))
                if signature in attempted:
                    continue
                attempted.add(signature)
                progressed = True

                result = _call_tool(tool, name, question, claims, arguments)
                tool_results.append(result)
                if result.get("ok") is True:
                    offline = False
            if not progressed:
                break
            continue

        if isinstance(raw, dict) and raw.get("final_answer"):
            answer = str(raw["final_answer"])
        elif tool_results:
            answer = _format_tool_results(tool_results)
        else:
            answer = _offline_answer(vault_evidence)
        break

    if tool_results and not any(item.get("ok") is True for item in tool_results):
        offline = True
        answer = _offline_answer(vault_evidence)

    return {
        "answer": answer,
        "offline": offline,
        "claims": claims,
        "tool_results": tool_results,
        "vault_evidence": vault_evidence,
    }
