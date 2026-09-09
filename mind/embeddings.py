from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    import torch  # type: ignore
except Exception:  # pragma: no cover
    torch = None


class Embedder:
    def embed(self, text: str) -> list[float]:
        if torch is not None:
            return self._torch_embed(text)
        return self._fallback_embed(text)

    def _torch_embed(self, text: str) -> list[float]:
        if torch is None:
            return self._fallback_embed(text)
        vector = torch.zeros(8, dtype=torch.float32)
        for index, char in enumerate(text[:8]):
            vector[index] = ord(char) % 10
        return vector.tolist()

    def _fallback_embed(self, text: str) -> list[float]:
        tokens = [token.lower() for token in text.replace("-", " ").split()]
        values = [0.0] * 8
        for index, token in enumerate(tokens[:8]):
            values[index] = float(sum(ord(ch) for ch in token) % 23) / 23.0
        return values


@dataclass
class DeterministicEmbedder(Embedder):
    seed: int = 0

    def embed(self, text: str) -> list[float]:
        values = [0.0] * 8
        for index, char in enumerate(text[:8]):
            values[index] = ((ord(char) + self.seed) % 13) / 13.0
        return values
