from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

try:
    from anthropic import Anthropic  # type: ignore
except Exception:  # pragma: no cover
    Anthropic = None  # type: ignore


def _load_dotenv_file() -> None:
    env_paths = [Path.cwd() / ".env", Path.cwd() / ".env.local"]
    for env_path in env_paths:
        if not env_path.exists():
            continue
        try:
            raw_text = env_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw_line in raw_text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


_load_dotenv_file()


class ConfigurationError(RuntimeError):
    """Raised when the Anthropic client is used without a configured API key."""


# Transient server-side and rate-limit statuses. Anything else is a real failure and
# is raised immediately rather than retried.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# max_tokens covers thinking plus the visible answer on models that think by default,
# so a small ceiling can be spent entirely on thinking and return no text at all.
# 16000 is the recommended non-streaming default; it stays under the SDK HTTP timeout.
DEFAULT_MAX_TOKENS = 16000

# Ceiling on tool round trips in one turn, so a model that keeps asking cannot loop
# forever. Configurable per call.
MAX_TOOL_ITERATIONS = 6

_STRUCTURED_SYSTEM = (
    "Respond with valid JSON matching the provided schema. "
    "Do not include markdown fences or explanatory prose."
)


def _status_of(exc: BaseException) -> int | None:
    """Read the HTTP status off an SDK exception.

    The Anthropic SDK exposes ``status_code``; ``status`` is accepted as a fallback so
    a differently shaped error object still routes to the retry path.
    """
    for attribute in ("status_code", "status"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        value = getattr(response, "status_code", None)
        if isinstance(value, int):
            return value
    return None


def _blocks_of(response: Any) -> list[Any]:
    return list(getattr(response, "content", []) or [])


def _text_from_response(response: Any) -> str:
    """Text only. A response may also carry tool_use and thinking blocks."""
    chunks: list[str] = []
    for block in _blocks_of(response):
        if getattr(block, "type", None) == "text":
            chunks.append(getattr(block, "text", "") or "")
    return "".join(chunks)


def _tool_use_blocks(response: Any) -> list[Any]:
    return [block for block in _blocks_of(response) if getattr(block, "type", None) == "tool_use"]


def _block_to_param(block: Any) -> dict[str, Any] | None:
    """Turn a response block back into something that can be sent as history."""
    block_type = getattr(block, "type", None)
    if block_type == "text":
        return {"type": "text", "text": getattr(block, "text", "") or ""}
    if block_type == "tool_use":
        return {
            "type": "tool_use",
            "id": getattr(block, "id", "") or "",
            "name": getattr(block, "name", "") or "",
            "input": getattr(block, "input", {}) or {},
        }
    if block_type == "thinking":
        # Thinking blocks are echoed back unchanged when the conversation continues on
        # the same model; dropping them invalidates the reasoning the model carries.
        payload: dict[str, Any] = {"type": "thinking", "thinking": getattr(block, "thinking", "") or ""}
        signature = getattr(block, "signature", None)
        if signature:
            payload["signature"] = signature
        return payload
    return None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def tool_schemas(tools: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Describe a tool registry to the Messages API."""
    schemas: list[dict[str, Any]] = []
    for name, tool in (tools or {}).items():
        schemas.append(
            {
                "name": getattr(tool, "name", None) or name,
                "description": getattr(tool, "description", None) or f"Tool for {name}",
                "input_schema": getattr(tool, "input_schema", None)
                or {"type": "object", "properties": {}, "additionalProperties": True},
            }
        )
    return schemas


def _with_provenance(name: str, result: Any) -> dict[str, Any]:
    """Clause 4.2: a fact without a source and a fetch timestamp does not reach the user."""
    if not isinstance(result, dict):
        return {
            "tool": name,
            "source": name,
            "fetched_at": _iso_now(),
            "ok": False,
            "error": f"tool returned {type(result).__name__}, expected a mapping",
        }
    payload = dict(result)
    payload.setdefault("tool", name)
    payload.setdefault("source", name)
    payload.setdefault("fetched_at", _iso_now())
    payload.setdefault("ok", "error" not in payload)
    return payload


def execute_tool(tools: Mapping[str, Any], name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run one tool. A failure is a result, never an exception.

    Clause 4.7: a tool that fails removes its domain from the request. It does not fail
    the request, and it must not be confused with Kit lacking the capability.
    """
    tool = (tools or {}).get(name)
    if tool is None:
        return {
            "tool": name,
            "source": name,
            "fetched_at": _iso_now(),
            "ok": False,
            "error": f"unknown tool: {name}",
        }
    try:
        runner = getattr(tool, "run", None)
        result = runner(**arguments) if callable(runner) else tool(**arguments)
    except Exception as exc:
        logger.warning("tool %s failed", name, exc_info=True)
        return {
            "tool": name,
            "source": name,
            "fetched_at": _iso_now(),
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return _with_provenance(name, result)


@dataclass
class ConverseResult:
    """The outcome of an agentic turn.

    ``tool_results`` carries every result the loop produced, each with its source and
    fetch timestamp intact, so the caller can cite provenance rather than take the
    model's word for it.
    """

    text: str
    messages: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    iterations: int
    stopped_at_cap: bool = False

    @property
    def reached_any_source(self) -> bool:
        return any(item.get("ok") is True for item in self.tool_results)


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 2:
        return stripped
    body = lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:]
    return "\n".join(body).strip()


@dataclass
class Opus5Gateway:
    model: str = "claude-opus-5"
    api_key: str | None = None
    base_url: str | None = None

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = os.getenv("ANTHROPIC_API_KEY")
        if self.base_url is None:
            self.base_url = os.getenv("ANTHROPIC_BASE_URL")

    def _client(self):
        if not self.api_key:
            raise ConfigurationError(
                "Anthropic API key is not configured. Set ANTHROPIC_API_KEY or pass api_key to Opus5Gateway."
            )
        if Anthropic is None:
            raise RuntimeError("anthropic package is required to use Opus5Gateway.")
        return Anthropic(api_key=self.api_key, base_url=self.base_url)

    def _request(
        self,
        *,
        messages: list[dict[str, Any]],
        system: str | None = None,
        temperature: float,
        max_tokens: int,
        effort: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ):
        client = self._client()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        # Without this the model has no tools at all, whatever the prompt says, and it
        # correctly reports that it cannot reach anything.
        if tools:
            kwargs["tools"] = tools
        # Opus 5 runs adaptive thinking by default, and max_tokens caps thinking plus
        # visible output together. Effort is the supported way to tune that depth;
        # budget_tokens is rejected on this model.
        if effort:
            kwargs["output_config"] = {"effort": effort}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if system is not None:
            kwargs["system"] = system
        workspace_id = os.getenv("ANTHROPIC_WORKSPACE_ID")
        if workspace_id:
            kwargs["extra_headers"] = {"anthropic-workspace-id": workspace_id}

        last_exc: BaseException | None = None
        for attempt in range(5):
            try:
                return client.messages.create(**kwargs)
            except TypeError as exc:  # Anthropic 1.x removed the legacy temperature argument.
                # Only worth retrying while temperature is still in the payload; otherwise
                # the TypeError came from somewhere else and retrying loops for nothing.
                if "temperature" not in str(exc) or "temperature" not in kwargs:
                    raise
                kwargs.pop("temperature", None)
                last_exc = exc
            except Exception as exc:  # pragma: no cover - exercised through mocked HTTP layer in tests
                if _status_of(exc) not in RETRYABLE_STATUS or attempt == 4:
                    raise
                last_exc = exc
                time.sleep(2 ** attempt)

        raise RuntimeError("Anthropic request failed after 5 attempts.") from last_exc

    def generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        effort: str | None = None,
        system: str | None = None,
    ) -> str:
        response = self._request(
            messages=[{"role": "user", "content": prompt}],
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            effort=effort,
        )
        return _text_from_response(response)

    def generate_structured(
        self,
        prompt: str,
        schema: Any,
        *,
        temperature: float = 0.0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        effort: str | None = None,
    ) -> Any:
        if schema is None:
            raise ValueError("schema is required for structured generation.")
        # The schema is sent with the prompt; asking for "the provided schema" without
        # providing it is what made structured output unreliable.
        content = f"{prompt}\n\nSchema:\n{json.dumps(schema, sort_keys=True, default=str)}"
        response = self._request(
            messages=[{"role": "user", "content": content}],
            system=_STRUCTURED_SYSTEM,
            temperature=temperature,
            max_tokens=max_tokens,
            effort=effort,
        )
        text = _strip_code_fence(_text_from_response(response))
        if not text:
            raise ValueError("Structured generation returned empty content.")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Structured generation response was not valid JSON.") from exc
        return parsed

    def converse(
        self,
        messages: list[dict[str, Any]] | str,
        *,
        tools: Mapping[str, Any] | None = None,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        effort: str | None = None,
        max_iterations: int = MAX_TOOL_ITERATIONS,
    ) -> ConverseResult:
        """Run a turn, letting the model call tools until it is done.

        Each pass sends the transcript plus the tool schemas, executes any tool_use
        blocks that come back, appends the results, and goes round again. The loop ends
        when the model stops asking for tools, or at the iteration cap.
        """
        transcript: list[dict[str, Any]] = (
            [{"role": "user", "content": messages}] if isinstance(messages, str) else [dict(m) for m in messages]
        )
        schemas = tool_schemas(tools) if tools else []
        collected: list[dict[str, Any]] = []
        cap = max(1, int(max_iterations))
        text = ""
        iterations = 0
        stopped_at_cap = False

        for iterations in range(1, cap + 1):
            response = self._request(
                # A snapshot per request: the transcript keeps growing, and passing the
                # live list would let a later append rewrite what an earlier call sent.
                messages=list(transcript),
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
                effort=effort,
                tools=schemas or None,
            )
            text = _text_from_response(response)

            assistant_content = [param for param in (_block_to_param(b) for b in _blocks_of(response)) if param]
            if assistant_content:
                transcript.append({"role": "assistant", "content": assistant_content})

            calls = _tool_use_blocks(response)
            if not calls:
                break

            results_content: list[dict[str, Any]] = []
            for call in calls:
                name = str(getattr(call, "name", "") or "")
                arguments = getattr(call, "input", {}) or {}
                if not isinstance(arguments, dict):
                    arguments = {}
                result = execute_tool(tools or {}, name, arguments)
                collected.append(result)
                results_content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": getattr(call, "id", "") or "",
                        # Serialised whole, so source and fetched_at reach the model too.
                        "content": json.dumps(result, default=str),
                        "is_error": result.get("ok") is not True,
                    }
                )
            # All results for one assistant turn go back in a single user message.
            transcript.append({"role": "user", "content": results_content})
        else:
            # The loop ran the full cap with the model still asking for tools.
            stopped_at_cap = True
            logger.warning("tool loop hit its %s iteration cap", cap)

        return ConverseResult(
            text=text,
            messages=transcript,
            tool_results=collected,
            iterations=iterations,
            stopped_at_cap=stopped_at_cap,
        )

    def plan(self, task: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "task": task,
            "strategy": "evidence-first, structural, falsifiable",
            "offline": self.api_key is None,
        }
