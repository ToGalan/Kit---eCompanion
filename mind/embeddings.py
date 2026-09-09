from __future__ import annotations

import math
from dataclasses import dataclass

try:
    from sentence_transformers import SentenceTransformer  # type: ignore
except Exception:  # pragma: no cover
    SentenceTransformer = None  # type: ignore


class Embedder:
    """Real sentence embedding model used for semantic comparison in production code."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self.model_name = model_name
        self._model = None

    def _load_model(self):
        if SentenceTransformer is None:
            raise ImportError("sentence-transformers is required for real embeddings.")
        if self._model is None:
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, text: str) -> list[float]:
        model = self._load_model()
        vector = model.encode(text, normalize_embeddings=True, convert_to_numpy=True)
        return [float(value) for value in vector.tolist()]


@dataclass
class StubEmbedder(Embedder):
    """Test-only deterministic embedder. Application code should not depend on this.

    It produces a fixed-length vector for repeatable tests without downloading a model.
    """

    seed: int = 0

    def embed(self, text: str) -> list[float]:
        values = [0.0] * 8
        for index, char in enumerate(text[:8]):
            values[index] = ((ord(char) + self.seed) % 13) / 13.0
        return values


DeterministicEmbedder = StubEmbedder


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    vec_a = Embedder().embed(a)
    vec_b = Embedder().embed(b)
    if len(vec_a) != len(vec_b):
        size = min(len(vec_a), len(vec_b))
        vec_a = vec_a[:size]
        vec_b = vec_b[:size]
    dot = sum(x * y for x, y in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(x * x for x in vec_a))
    norm_b = math.sqrt(sum(x * x for x in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
