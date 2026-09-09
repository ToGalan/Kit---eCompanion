from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

try:
    from anthropic import Anthropic  # type: ignore
except Exception:  # pragma: no cover
    Anthropic = None  # type: ignore


class ConfigurationError(RuntimeError):
    """Raised when the Anthropic client is used without a configured API key."""


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

    def _request(self, *, messages: list[dict[str, Any]], system: str | None = None, temperature: float, max_tokens: int):
        client = self._client()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system is not None:
            kwargs["system"] = system

        for attempt in range(5):
            try:
                response = client.messages.create(**kwargs)
                return response
            except Exception as exc:  # pragma: no cover - exercised through mocked HTTP layer in tests
                status = getattr(exc, "status", None)
                if status not in {429, 500, 502, 503, 504} or attempt == 4:
                    raise
                time.sleep(2 ** attempt)

    def generate(self, prompt: str, *, temperature: float = 0.0, max_tokens: int = 512) -> str:
        response = self._request(
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        blocks = getattr(response, "content", []) or []
        text_chunks = []
        for block in blocks:
            if getattr(block, "type", None) == "text":
                text_chunks.append(getattr(block, "text", ""))
        if not text_chunks:
            return ""
        return "".join(text_chunks)

    def generate_structured(self, prompt: str, schema: Any, *, temperature: float = 0.0, max_tokens: int = 512) -> Any:
        if schema is None:
            raise ValueError("schema is required for structured generation.")
        response = self._request(
            messages=[{"role": "user", "content": prompt}],
            system=(
                "Respond with valid JSON matching the provided schema. "
                "Do not include markdown fences or explanatory prose."
            ),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = self.generate(prompt, temperature=temperature, max_tokens=max_tokens)
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
