from __future__ import annotations

from pathlib import Path
from typing import Any

from .ai import Opus5Gateway
from .embeddings import DeterministicEmbedder, Embedder


class ObsidianBrain:
    def __init__(self, vault_root: str | Path | None = None, model: Opus5Gateway | None = None):
        self.vault_root = Path(vault_root) if vault_root else Path("vault")
        self.vault_root.mkdir(parents=True, exist_ok=True)
        self.model = model or Opus5Gateway()
        self.embedder: Embedder = DeterministicEmbedder()

    def ingest(self, title: str, body: str) -> dict[str, Any]:
        note = self.vault_root / f"{title}.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(f"---\ntitle: {title}\n---\n\n# {title}\n\n{body}\n", encoding="utf-8")
        return {"title": title, "path": str(note), "embedding": self.embedder.embed(body)}

    def summarize(self, title: str, body: str) -> str:
        summary = self.model.generate(f"Summarize the claim structure for {title}: {body[:200]}")
        return summary

    def route(self, prompt: str) -> str:
        lowered = prompt.lower()
        if "question" in lowered or "what" in lowered:
            return "question"
        if "claim" in lowered or "assert" in lowered:
            return "claim"
        return "question"
