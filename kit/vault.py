from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class Vault:
    def __init__(self, root: str | Path):
        self.path = Path(root)
        self.path.mkdir(parents=True, exist_ok=True)
        for folder in ("claims", "works", "questions", "sources"):
            (self.path / folder).mkdir(parents=True, exist_ok=True)

    def _slug(self, title: str) -> str:
        cleaned = re.sub(r"\s+", " ", (title or "untitled").strip())
        cleaned = cleaned.strip(" .") or "untitled"
        invalid = re.compile(r"[\\/:*?\"<>|]")
        cleaned = invalid.sub("-", cleaned)
        return cleaned

    def _note_path(self, kind: str, title: str) -> Path:
        base = self.path / kind
        return base / f"{self._slug(title)}.md"

    def _read_note(self, path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def _frontmatter(self, *, title: str, status: str = "proposed", dimension: str | None = None, falsifier: str | None = None, confidence: float | None = None, work: str | None = None, evidence: list[str] | None = None) -> str:
        lines = ["---", f"title: {title}", f"status: {status}"]
        if dimension:
            lines.append(f"dimension: {dimension}")
        if falsifier:
            lines.append(f"falsifier: {falsifier}")
        if confidence is not None:
            lines.append(f"confidence: {confidence}")
        if work:
            lines.append(f"work: {work}")
        if evidence:
            lines.append(f"evidence: [{', '.join(f'\"{item}\"' for item in evidence)}]")
        lines.append("---")
        return "\n".join(lines) + "\n\n"

    def _parse_frontmatter(self, content: str) -> dict[str, Any]:
        if not content.startswith("---\n"):
            return {}
        try:
            end = content.index("\n---\n", 4)
        except ValueError:
            return {}
        headers = content[4:end].splitlines()
        parsed: dict[str, Any] = {}
        for line in headers:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            parsed[key.strip()] = value.strip()
        return parsed

    def _render_markdown(self, *, title: str, body: str, **meta: Any) -> str:
        frontmatter = self._frontmatter(title=title, **meta)
        return f"{frontmatter}# {title}\n\n{body.strip()}\n"

    def _preserve_notes_section(self, existing: str, new_body: str) -> str:
        existing_without_frontmatter = existing
        if existing_without_frontmatter.startswith("---\n"):
            try:
                end = existing_without_frontmatter.index("\n---\n", 4)
            except ValueError:
                end = -1
            if end != -1:
                existing_without_frontmatter = existing_without_frontmatter[end + 5 :]

        new_body_text = new_body.strip()
        if "## Notes" in new_body_text:
            return new_body_text + "\n"

        existing_match = re.search(r"(?ms)^## Notes\s*\n.*?(?=\n## |\Z)", existing_without_frontmatter)
        if not existing_match:
            return new_body_text + "\n"

        notes_block = existing_match.group(0).rstrip()
        return f"{new_body_text.rstrip()}\n\n{notes_block}\n"

    def write_note(self, kind: str, title: str, content: str, **meta: Any) -> Path:
        path = self._note_path(kind, title)
        existing = self._read_note(path)
        if existing:
            content = self._preserve_notes_section(existing, content)
        rendered = self._render_markdown(title=title, body=content, **meta)
        path.write_text(rendered, encoding="utf-8")
        return path

    def rewrite_note(self, title: str, content: str, kind: str = "works") -> Path:
        path = self._note_path(kind, title)
        existing = self._read_note(path)
        body = content.strip()
        if existing:
            body = self._preserve_notes_section(existing, body)
        body = re.sub(r"(?s)^# .*?\n+", "", body.lstrip())
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {title}\n\n{body.strip()}\n", encoding="utf-8")
        return path

    def get_note(self, title: str, kind: str | None = None) -> dict[str, Any] | None:
        for folder in ([kind] if kind else ["claims", "works", "questions", "sources"]):
            target = self._note_path(folder, title)
            if target.exists():
                content = target.read_text(encoding="utf-8")
                return {"title": title, "path": str(target), "content": content, "kind": folder}
        return None

    def write_work(self, title: str, content: str, **meta: Any) -> Path:
        return self.write_note("works", title, content, **meta)

    def write_source(self, title: str, content: str, **meta: Any) -> Path:
        return self.write_note("sources", title, content, **meta)

    def write_question(self, title: str, content: str, **meta: Any) -> Path:
        return self.write_note("questions", title, content, **meta)

    def write_claim(
        self,
        title: str,
        content: str,
        *,
        falsifier: str | None,
        dimension: str | None = None,
        confidence: float | None = None,
        work: str | None = None,
        evidence: list[str] | None = None,
        status: str = "proposed",
    ) -> Path:
        if not falsifier or not str(falsifier).strip():
            raise ValueError("A claim requires a falsifier.")
        return self.write_note(
            "claims",
            title,
            content,
            status=status,
            dimension=dimension,
            falsifier=falsifier,
            confidence=confidence,
            work=work,
            evidence=evidence or [],
        )

    def drop_source(self, source_id: str) -> None:
        source_path = self.path / "sources" / f"{source_id}.md"
        if source_path.exists():
            source_path.unlink()

        for claim_path in sorted((self.path / "claims").glob("*.md")):
            text = claim_path.read_text(encoding="utf-8")
            meta = self._parse_frontmatter(text)
            evidence = meta.get("evidence", "[]")
            try:
                items = [item.strip().strip('"') for item in evidence.strip("[]").split(",") if item.strip()]
            except Exception:
                items = []
            if source_id in items:
                items = [item for item in items if item != source_id]
                if not items:
                    claim_path.unlink()
                    continue
                meta["evidence"] = str(items)
                updated = re.sub(r"(?ms)^evidence: .*?$", f"evidence: [{', '.join(f'\"{item}\"' for item in items)}]", text, count=1)
                claim_path.write_text(updated, encoding="utf-8")

    def backlinks(self, work: str) -> list[dict[str, Any]]:
        backlinks: list[dict[str, Any]] = []
        for claim_path in sorted((self.path / "claims").glob("*.md")):
            text = claim_path.read_text(encoding="utf-8")
            if f"[[{work}]]" not in text and self._parse_frontmatter(text).get("work") != work:
                continue
            title = claim_path.stem
            backlinks.append({"title": title, "path": str(claim_path), "content": text})
        return backlinks


def write_claim(vault: Vault, *, title: str, content: str, falsifier: str | None, **kwargs: Any) -> Path:
    return vault.write_claim(title=title, content=content, falsifier=falsifier, **kwargs)
