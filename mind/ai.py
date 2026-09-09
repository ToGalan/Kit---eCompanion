from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


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

    def generate(self, prompt: str, *, temperature: float = 0.0, max_tokens: int = 512) -> str:
        if not self.api_key:
            return (
                "Opus 5 is configured but no API key is present; using offline fallback reasoning. "
                "The returned answer is intentionally conservative and evidence-first."
            )
        return (
            f"[Opus 5 route via {self.model}]\n"
            f"Prompt: {prompt[:160]}\n"
            f"Response: This system would reason over the record, but the live model is not available in this environment."
        )

    def plan(self, task: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "task": task,
            "strategy": "evidence-first, structural, falsifiable",
            "offline": self.api_key is None,
        }
