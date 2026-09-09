from __future__ import annotations

import json
from typing import Any

from mind.ai import Opus5Gateway
from mind.tools import DEFAULT_TOOLS, Tool


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
        if result.get("ok"):
            pieces.append(f"{tool_name}: found live evidence from {result.get('source', tool_name)}.")
        else:
            pieces.append(f"{tool_name}: unavailable ({result.get('error', 'no detail')}).")
    return " ".join(pieces)


def run_turn(question: str, claims: list[dict[str, Any]], catalogs: dict[str, Any] | None = None) -> dict[str, Any]:
    tool_map: dict[str, Any] = catalogs if catalogs else DEFAULT_TOOLS
    gateway = Opus5Gateway()
    answer = (
        "I do not have live catalog access right now, so I’m answering from the local record and "
        "marking this as an offline result. The record should be treated as a provisional, evidence-based answer."
    )
    tool_results: list[dict[str, Any]] = []
    offline = True

    for _ in range(6):
        payload: dict[str, Any] = {
            "question": question,
            "claims": claims,
            "tools": _tool_schema_list(tool_map),
        }
        try:
            raw = gateway.generate_structured(
                json.dumps(payload, sort_keys=True),
                {"type": "object", "properties": {"tool_calls": {"type": "array"}, "final_answer": {"type": "string"}}, "additionalProperties": True},
                temperature=0.1,
                max_tokens=512,
            )
        except Exception:
            raw = {"final_answer": answer}

        tool_calls = raw.get("tool_calls") if isinstance(raw, dict) else None
        if isinstance(tool_calls, list) and tool_calls:
            for call in tool_calls:
                name = str(call.get("name", "")).strip()
                if not name:
                    continue
                tool = tool_map.get(name)
                if tool is None:
                    continue
                arguments = call.get("arguments", {}) if isinstance(call, dict) else {}
                try:
                    if hasattr(tool, "run"):
                        result = tool.run(**arguments)
                    else:
                        result = tool(question, claims, **arguments)
                except Exception as exc:
                    result = {"tool": name, "source": name, "ok": False, "fetched_at": None, "error": str(exc)}
                tool_results.append(result)
                if result.get("ok") is True:
                    offline = False
            if not any(item.get("ok") is True for item in tool_results):
                break
            continue

        if isinstance(raw, dict) and raw.get("final_answer"):
            answer = str(raw["final_answer"])
        else:
            if tool_results:
                answer = _format_tool_results(tool_results)
            else:
                answer = (
                    "I could not reach any configured catalog or web source, so I’m marking this result as offline. "
                    "The record remains provisional and evidence-first."
                )
        break

    if tool_results and not any(item.get("ok") is True for item in tool_results):
        offline = True
        answer = (
            "I could not reach any configured catalog or web source, so I’m marking this result as offline. "
            "The record remains provisional and evidence-first."
        )

    return {"answer": answer, "offline": offline, "claims": claims, "tool_results": tool_results}
