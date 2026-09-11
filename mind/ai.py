from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def _text_from_response(response: Any) -> str:
    blocks = getattr(response, "content", []) or []
    chunks: list[str] = []
    for block in blocks:
        if getattr(block, "type", None) == "text":
            chunks.append(getattr(block, "text", "") or "")
    return "".join(chunks)


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
    ):
        client = self._client()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
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

    def plan(self, task: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "task": task,
            "strategy": "evidence-first, structural, falsifiable",
            "offline": self.api_key is None,
        }
