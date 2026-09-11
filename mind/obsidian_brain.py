from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Iterable

from kit.vault import Vault, body_from_markdown, parse_frontmatter

from .ai import Opus5Gateway
from .embeddings import Embedder, cosine, shared_embedder

logger = logging.getLogger(__name__)

# The typed folders Vault manages. Untyped notes sitting at the vault root are also
# mapped, so material written before the vault had folders stays reachable.
NOTE_KINDS = ("persona", "hypotheses", "works", "claims", "sources", "questions")

# [[Target]], [[Target|alias]] and [[Target#heading]] all resolve to "Target".
_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")

_QUESTION_PREFIXES = ("what", "who", "when", "where", "why", "how", "which", "is ", "are ", "does ", "did ", "can ")
_CLAIM_MARKERS = ("claim", "assert", "argue", "i think", "i believe", "the point is")


def wikilinks_in(text: str) -> list[str]:
    """Return the wikilink targets in a body, in order, without duplicates."""
    seen: list[str] = []
    for raw in _WIKILINK.findall(text or ""):
        target = re.sub(r"\s+", " ", raw).strip()
        if target and target not in seen:
            seen.append(target)
    return seen


class Note:
    """One vault note, carrying the provenance the constitution requires travels with it."""

    __slots__ = ("title", "kind", "path", "body", "layer", "source", "confidence", "updated", "links")

    def __init__(self, *, title: str, kind: str, path: Path, content: str):
        meta = parse_frontmatter(content)
        self.title = title
        self.kind = kind
        self.path = path
        self.body = body_from_markdown(content)
        # The layer is never guessed. A note without one reports None rather than being
        # defaulted to "elicited", which would claim the user said something they did not.
        layer = str(meta.get("layer") or "").strip().lower()
        self.layer = layer or None
        self.source = meta.get("source")
        self.confidence = meta.get("confidence")
        self.updated = meta.get("updated")
        self.links = wikilinks_in(self.body)

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "kind": self.kind,
            "path": str(self.path),
            "body": self.body,
            "layer": self.layer,
            "source": self.source,
            "confidence": self.confidence,
            "updated": self.updated,
            "links": list(self.links),
        }


class ObsidianBrain:
    """AI plus the vault's own link graph.

    Writes go through Vault, so every note gets frontmatter, a layer, a source and a git
    commit carrying a reason. Reads map the wikilink graph and, where an embedder is
    available, semantic neighbours, so the model can be grounded in what the vault holds
    rather than answering from nothing.
    """

    def __init__(
        self,
        vault_root: str | Path | None = None,
        model: Opus5Gateway | None = None,
        *,
        vault: Vault | None = None,
        embedder: Embedder | None = None,
        default_kind: str = "works",
    ):
        self.vault = vault if vault is not None else Vault(vault_root or "vault")
        self.vault_root = self.vault.path
        self.model = model or Opus5Gateway()
        # Real embeddings by default. Constructing an Embedder is cheap; the model only
        # loads on first embed, so nothing downloads until semantic mapping is used.
        self.embedder = embedder if embedder is not None else shared_embedder()
        self.default_kind = default_kind
        self._embedding_cache: dict[str, list[float]] = {}

    # ------------------------------------------------------------------ writing

    def ingest(
        self,
        title: str,
        body: str,
        *,
        kind: str | None = None,
        reason: str = "brain ingest",
        layer: str | None = None,
        source: str | None = None,
        **meta: Any,
    ) -> dict[str, Any]:
        """Write a note through the vault so it is typed, versioned and attributable."""
        clean_title = re.sub(r"\s+", " ", str(title or "")).strip()
        if not clean_title:
            raise ValueError("A note requires a title.")
        note_kind = (kind or self.default_kind).strip().lower()
        if note_kind not in NOTE_KINDS:
            raise ValueError(f"kind must be one of: {', '.join(NOTE_KINDS)}.")
        if layer is not None:
            meta["layer"] = layer
        if source is not None:
            meta["source"] = source

        path = self.vault.write_note(note_kind, clean_title, body, reason=reason, **meta)
        self._embedding_cache.clear()
        return {
            "title": clean_title,
            "kind": note_kind,
            "path": str(path),
            "links": wikilinks_in(body),
        }

    # ------------------------------------------------------------------ mapping

    def notes(self) -> list[Note]:
        collected: list[Note] = []
        for kind in NOTE_KINDS:
            folder = self.vault.path / kind
            if not folder.exists():
                continue
            for path in sorted(folder.glob("*.md")):
                collected.append(self._read(kind, path))
        for path in sorted(self.vault.path.glob("*.md")):
            collected.append(self._read("", path))
        return collected

    def _read(self, kind: str, path: Path) -> Note:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("could not read vault note %s", path, exc_info=True)
            content = ""
        return Note(title=path.stem, kind=kind, path=path, content=content)

    def _index(self, notes: Iterable[Note] | None = None) -> dict[str, Note]:
        return {note.title.lower(): note for note in (notes if notes is not None else self.notes())}

    def links(self, title: str) -> list[dict[str, Any]]:
        """Notes this note points at, resolved against what the vault actually holds."""
        notes = self.notes()
        index = self._index(notes)
        origin = index.get(str(title).strip().lower())
        if origin is None:
            return []
        resolved: list[dict[str, Any]] = []
        for target in origin.links:
            match = index.get(target.lower())
            if match is not None and match.title != origin.title:
                resolved.append(match.as_dict())
        return resolved

    def backlinks(self, title: str) -> list[dict[str, Any]]:
        """Notes that point at this note."""
        wanted = str(title).strip().lower()
        if not wanted:
            return []
        found: list[dict[str, Any]] = []
        for note in self.notes():
            if note.title.lower() == wanted:
                continue
            if any(link.lower() == wanted for link in note.links):
                found.append(note.as_dict())
        return found

    def _embed(self, text: str) -> list[float] | None:
        key = text.strip()
        if not key:
            return None
        cached = self._embedding_cache.get(key)
        if cached is not None:
            return cached
        try:
            vector = self.embedder.embed(key)
        except Exception:
            # No embedder available is a degraded mapping, not a failed request.
            logger.warning("embedding unavailable; falling back to link-only mapping", exc_info=True)
            return None
        self._embedding_cache[key] = vector
        return vector

    def neighbours(self, text: str, *, limit: int = 5) -> list[dict[str, Any]]:
        """Notes closest to the given text by embedding similarity."""
        notes = [note for note in self.notes() if note.body.strip()]
        if not notes:
            return []
        query = self._embed(text)
        if query is None:
            return []
        scored: list[tuple[float, Note]] = []
        for note in notes:
            vector = self._embed(f"{note.title} {note.body}")
            if vector is None:
                continue
            scored.append((cosine(query, vector), note))
        scored.sort(key=lambda pair: (-pair[0], pair[1].title.lower()))
        results: list[dict[str, Any]] = []
        for score, note in scored[: max(0, int(limit))]:
            payload = note.as_dict()
            payload["similarity"] = round(score, 4)
            results.append(payload)
        return results

    def related(self, question: str, *, limit: int = 5, seed_title: str | None = None) -> list[dict[str, Any]]:
        """Vault evidence for a question: semantic neighbours widened by the link graph.

        Every entry carries its layer and source, so a caller can tell what the user
        actually said apart from what Kit concluded.
        """
        collected: dict[str, dict[str, Any]] = {}

        def add(payload: dict[str, Any], relation: str) -> None:
            key = str(payload.get("path") or payload.get("title"))
            if key in collected:
                return
            entry = dict(payload)
            entry["relation"] = relation
            collected[key] = entry

        if seed_title:
            index = self._index()
            seed = index.get(str(seed_title).strip().lower())
            if seed is not None:
                add(seed.as_dict(), "seed")
            for payload in self.links(seed_title):
                add(payload, "link")
            for payload in self.backlinks(seed_title):
                add(payload, "backlink")

        for payload in self.neighbours(question, limit=limit):
            add(payload, "semantic")

        # One hop out from whatever matched, which is the part that makes this a map
        # rather than a search: a neighbour's neighbours are context too.
        for title in [entry["title"] for entry in list(collected.values())]:
            for payload in self.links(title):
                add(payload, "link")

        return list(collected.values())[: max(0, int(limit))]

    # ------------------------------------------------------------------ ai

    def summarize(self, title: str, body: str) -> str:
        try:
            summary = self.model.generate(f"Summarize the claim structure for {title}: {body[:200]}")
            if summary:
                return summary
        except Exception:
            logger.warning("summarize fell back to the local excerpt for %s", title, exc_info=True)
        return f"{title}: {body[:200]}"

    def route(self, prompt: str) -> str:
        lowered = re.sub(r"\s+", " ", str(prompt or "")).strip().lower()
        if not lowered:
            return "question"
        # A question mark settles it, before any keyword is consulted; "what is the
        # central claim?" is a question about a claim, not a claim.
        if lowered.endswith("?") or lowered.startswith(_QUESTION_PREFIXES):
            return "question"
        if any(marker in lowered for marker in _CLAIM_MARKERS):
            return "claim"
        return "question"
