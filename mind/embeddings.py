from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass

# huggingface_hub reads this once, at import, into a module constant. Setting it later
# has no effect, so it has to be set before sentence_transformers is imported. Offline
# means "use the cached model"; the freshness check it skips costs about 25 seconds of
# SSL retries per file on a machine that cannot verify huggingface.co's certificate,
# and the result is discarded anyway. Export HF_HUB_OFFLINE=0 to force the online path.
os.environ.setdefault("HF_HUB_OFFLINE", "1")

try:
    from sentence_transformers import SentenceTransformer  # type: ignore
except Exception:  # pragma: no cover
    SentenceTransformer = None  # type: ignore

logger = logging.getLogger(__name__)


class Embedder:
    """Real sentence embedding model used for semantic comparison in production code."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self.model_name = model_name
        self._model = None

    def _load_model(self):
        if SentenceTransformer is None:
            raise ImportError("sentence-transformers is required for real embeddings.")
        if self._model is None:
            self._model = self._load_preferring_cache()
        return self._model

    def _load_preferring_cache(self):
        """Load from the local cache first, falling back to a download.

        The hub freshness check costs about 25 seconds of SSL retries per model file
        on a machine that cannot verify huggingface.co's certificate, and the answer
        is discarded anyway once the cached copy is used. Trying the cache first turns
        that into a local read; a machine without the model still downloads it.
        """
        try:
            return SentenceTransformer(self.model_name)
        except Exception:
            logger.info("%s is not in the local cache; trying to download it", self.model_name)

        # Cache miss. Flip the already-imported constant, since the environment
        # variable is only read at import time and changing it now would do nothing.
        try:
            from huggingface_hub import constants as hf_constants  # type: ignore

            hf_constants.HF_HUB_OFFLINE = False
        except Exception:  # pragma: no cover - hub internals moved
            logger.warning("could not switch huggingface_hub out of offline mode", exc_info=True)
        return SentenceTransformer(self.model_name)

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


_SHARED_EMBEDDER: Embedder | None = None


def shared_embedder() -> Embedder:
    """One process-wide Embedder, so the model is loaded once rather than per call."""
    global _SHARED_EMBEDDER
    if _SHARED_EMBEDDER is None:
        _SHARED_EMBEDDER = Embedder()
    return _SHARED_EMBEDDER


def cosine(vec_a: list[float], vec_b: list[float]) -> float:
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


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    embedder = shared_embedder()
    vec_a = embedder.embed(a)
    vec_b = embedder.embed(b)
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
