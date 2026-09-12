from __future__ import annotations

import json
import logging
import re
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_frontmatter(content: str) -> dict[str, Any]:
    """Read the YAML-ish frontmatter block off a note.

    Public because the brain and the API both need to read a note's layer, source and
    occasion without reaching into Vault internals.
    """
    if not content.startswith("---\n"):
        return {}
    try:
        end = content.index("\n---\n", 4)
    except ValueError:
        return {}
    parsed: dict[str, Any] = {}
    for line in content[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key.strip()] = value.strip()
    return parsed


def parse_evidence(value: Any) -> list[str]:
    """Read an evidence list back off frontmatter.

    Frontmatter is flat text, so the list written as `["a", "b"]` comes back as a
    string. Public because evidence counts are what confidence is computed from.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text or text == "[]":
        return []
    quoted = re.findall(r'"([^"]*)"', text)
    if quoted:
        return [item.strip() for item in quoted if item.strip()]
    return [segment.strip().strip("[]\"'") for segment in text.split(",") if segment.strip().strip("[]\"'")]


def body_from_markdown(content: str) -> str:
    """Return a note's prose with the frontmatter block and the H1 title removed."""
    if content.startswith("---\n"):
        try:
            end = content.index("\n---\n", 4)
        except ValueError:
            return content.strip()
        content = content[end + 5 :]
    body = re.sub(r"(?ms)^# .*?\n+", "", content.lstrip())
    return body.strip()


# A transcript line. Written one turn per line so the note stays a readable markdown
# document in Obsidian and still parses back into turns without a second store.
_TURN_LINE = re.compile(r"^- (?P<timestamp>\S+) \*\*(?P<speaker>user|kit)\*\*: (?P<text>.*)$")

SPEAKERS = ("user", "kit")


class Occasion:
    def __init__(
        self,
        *,
        time_of_day: str,
        day_type: str,
        session_length: str,
        label: str | None = None,
    ):
        self.time_of_day = self._normalize_bucket(time_of_day, "time_of_day", {"morning", "afternoon", "evening", "night"})
        self.day_type = self._normalize_bucket(day_type, "day_type", {"weekday", "weekend"})
        self.session_length = self._normalize_bucket(session_length, "session_length", {"short", "medium", "open", "unknown"})
        self.label = label.strip() if isinstance(label, str) and label.strip() else None

    @staticmethod
    def _normalize_bucket(value: str | None, field: str, allowed: set[str]) -> str:
        if value is None or not str(value).strip():
            raise ValueError(f"Occasion.{field} is required.")
        normalized = str(value).strip().lower()
        if normalized not in allowed:
            raise ValueError(f"Occasion.{field} must be one of: {', '.join(sorted(allowed))}.")
        return normalized

    def as_dict(self) -> dict[str, str | None]:
        return {
            "time_of_day": self.time_of_day,
            "day_type": self.day_type,
            "session_length": self.session_length,
            "label": self.label,
        }

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Occasion):
            return NotImplemented
        return self.as_dict() == other.as_dict()

    def __hash__(self) -> int:
        return hash(tuple(sorted(self.as_dict().items())))


class Vault:
    def __init__(self, root: str | Path):
        self.path = Path(root)
        self.path.mkdir(parents=True, exist_ok=True)
        for folder in ("claims", "works", "questions", "sources", "persona", "hypotheses", "conversations"):
            (self.path / folder).mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(__name__)
        self._ensure_git_repo()

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

    def _run_git(self, *args: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=str(self.path),
                capture_output=True,
                text=True,
                check=True,
            )
        except Exception as exc:  # pragma: no cover - defensive logging path
            self.logger.warning("git %s failed in %s: %s", " ".join(args), self.path, exc)
            return None

    def _ensure_git_repo(self) -> None:
        if (self.path / ".git").exists():
            return
        if self._run_git("init") is None:
            return
        for key, value in {"user.name": "Kit Vault", "user.email": "vault@local"}.items():
            self._run_git("config", key, value)

    @staticmethod
    def _iso_timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _frontmatter(
        self,
        *,
        title: str,
        status: str = "proposed",
        dimension: str | None = None,
        falsifier: str | None = None,
        confidence: float | None = None,
        work: str | None = None,
        evidence: list[str] | None = None,
        updated: str | None = None,
        layer: str | None = None,
        source: str | None = None,
        function: str | None = None,
        occasion: Occasion | None = None,
        user: str | None = None,
    ) -> str:
        lines = ["---", f"title: {title}", f"status: {status}"]
        lines.append(f"updated: {updated or self._iso_timestamp()}")
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
        if layer:
            lines.append(f"layer: {layer}")
        if source:
            lines.append(f"source: {source}")
        if user:
            lines.append(f"user: {user}")
        if function:
            lines.append(f"function: {function}")
        if occasion is not None:
            lines.append(f"occasion_time_of_day: {occasion.time_of_day}")
            lines.append(f"occasion_day_type: {occasion.day_type}")
            lines.append(f"occasion_session_length: {occasion.session_length}")
            if occasion.label:
                lines.append(f"occasion_label: {occasion.label}")
        lines.append("---")
        return "\n".join(lines) + "\n\n"

    def _parse_frontmatter(self, content: str) -> dict[str, Any]:
        return parse_frontmatter(content)

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

    def _commit_note(self, kind: str, title: str, reason: str) -> None:
        self._ensure_git_repo()
        git_add = self._run_git("add", "-A")
        if git_add is None:
            return
        message = f"{kind}/{title}: {reason}"
        self._run_git("commit", "-m", message)

    def write_note(self, kind: str, title: str, content: str, *, reason: str, **meta: Any) -> Path:
        if not reason or not str(reason).strip():
            raise ValueError("A note write requires a reason.")
        meta.setdefault("updated", self._iso_timestamp())
        path = self._note_path(kind, title)
        existing = self._read_note(path)
        if existing:
            content = self._preserve_notes_section(existing, content)
        rendered = self._render_markdown(title=title, body=content, **meta)
        path.write_text(rendered, encoding="utf-8")
        self._commit_note(kind, title, reason)
        return path

    def rewrite_note(self, title: str, content: str, kind: str = "works", *, reason: str = "rewrite") -> Path:
        if not reason or not str(reason).strip():
            raise ValueError("A note write requires a reason.")
        path = self._note_path(kind, title)
        existing = self._read_note(path)
        body = content.strip()
        if existing:
            body = self._preserve_notes_section(existing, body)
        body = re.sub(r"(?s)^# .*?\n+", "", body.lstrip())
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {title}\n\n{body.strip()}\n", encoding="utf-8")
        self._commit_note(kind, title, reason)
        return path

    def get_note(self, title: str, kind: str | None = None) -> dict[str, Any] | None:
        for folder in ([kind] if kind else ["claims", "works", "questions", "sources", "persona", "hypotheses", "conversations"]):
            target = self._note_path(folder, title)
            if target.exists():
                content = target.read_text(encoding="utf-8")
                return {"title": title, "path": str(target), "content": content, "kind": folder}
        return None

    def _body_from_markdown(self, content: str) -> str:
        return body_from_markdown(content)

    def append_conversation_turns(
        self,
        session_id: str,
        turns: list[tuple[str, str]],
        *,
        user: str,
        reason: str,
        occasion: Occasion | None = None,
        timestamp: str | None = None,
    ) -> Path:
        """Append an exchange to this session's transcript note.

        The whole exchange lands in one commit, so the vault history reads as a
        conversation rather than as a stream of half turns.
        """
        session = re.sub(r"\s+", " ", str(session_id or "")).strip()
        if not session:
            raise ValueError("A conversation turn requires a session id.")
        if not user or not str(user).strip():
            raise ValueError("A conversation turn requires a user.")

        stamp = timestamp or self._iso_timestamp()
        lines: list[str] = []
        for speaker, text in turns:
            speaker_name = str(speaker or "").strip().lower()
            if speaker_name not in SPEAKERS:
                raise ValueError(f"speaker must be one of: {', '.join(SPEAKERS)}.")
            # Collapsed to a single line: the transcript is parsed back line by line, and
            # a turn that spans lines would read as several turns on the way in.
            spoken = re.sub(r"\s+", " ", str(text or "")).strip()
            if not spoken:
                continue
            lines.append(f"- {stamp} **{speaker_name}**: {spoken}")
        if not lines:
            raise ValueError("A conversation turn requires text.")

        existing = self._read_note(self._note_path("conversations", session))
        body = body_from_markdown(existing) if existing else ""
        combined = "\n".join([part for part in [body.strip(), "\n".join(lines)] if part])

        return self.write_note(
            "conversations",
            session,
            combined,
            reason=reason,
            status="active",
            user=str(user).strip(),
            occasion=occasion,
        )

    def conversation_turns(self, session_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Read a session transcript back as turns, oldest first."""
        session = re.sub(r"\s+", " ", str(session_id or "")).strip()
        if not session:
            return []
        content = self._read_note(self._note_path("conversations", session))
        if not content:
            return []
        turns: list[dict[str, Any]] = []
        for line in body_from_markdown(content).splitlines():
            found = _TURN_LINE.match(line.strip())
            if found is None:
                continue
            turns.append(
                {
                    "speaker": found.group("speaker"),
                    "text": found.group("text").strip(),
                    "timestamp": found.group("timestamp"),
                }
            )
        if limit is not None and limit >= 0:
            return turns[-limit:] if limit else []
        return turns

    def conversation_sessions(self, *, user: str | None = None) -> list[dict[str, Any]]:
        """Every recorded session, most recently updated first."""
        wanted = str(user).strip() if user else None
        sessions: list[dict[str, Any]] = []
        for path in sorted((self.path / "conversations").glob("*.md")):
            meta = self._parse_frontmatter(path.read_text(encoding="utf-8"))
            owner = str(meta.get("user") or "").strip()
            if wanted is not None and owner != wanted:
                continue
            sessions.append({"session_id": path.stem, "user": owner, "updated": meta.get("updated") or ""})
        sessions.sort(key=lambda item: str(item.get("updated") or ""), reverse=True)
        return sessions

    def write_persona_fact(
        self,
        title: str,
        content: str,
        *,
        reason: str,
        layer: str,
        source: str,
        confidence: float | None = None,
        evidence: list[str] | None = None,
        user: str | None = None,
    ) -> Path:
        if layer not in {"elicited", "inferred", "observed"}:
            raise ValueError("layer must be one of elicited, inferred, observed.")
        if not source or not str(source).strip():
            raise ValueError("A persona fact requires a source.")
        return self.write_note(
            "persona",
            title,
            content,
            reason=reason,
            status="active",
            layer=layer,
            source=source,
            confidence=confidence,
            evidence=evidence or [],
            user=user,
        )

    def write_hypothesis(
        self,
        title: str,
        content: str,
        *,
        reason: str,
        falsifier: str | None,
        function: str,
        evidence: list[str] | None = None,
        confidence: float | None = None,
        status: str = "proposed",
        occasion: Occasion | None = None,
    ) -> Path:
        if not falsifier or not str(falsifier).strip():
            raise ValueError("A claim requires a falsifier.")
        if not function or not str(function).strip():
            raise ValueError("A hypothesis requires a function.")
        if occasion is None:
            # A hypothesis states that content serving some function fits this person in
            # this occasion. Without the occasion there is no claim, only a taste label,
            # so this refuses rather than inventing a plausible-looking evening slot.
            raise ValueError("A hypothesis requires an occasion.")
        return self.write_note(
            "hypotheses",
            title,
            content,
            reason=reason,
            status=status,
            falsifier=falsifier,
            function=function,
            evidence=evidence or [],
            confidence=confidence,
            occasion=occasion,
        )

    def _hypothesis_payload(self, path: Path, text: str) -> dict[str, Any]:
        meta = self._parse_frontmatter(text)
        occasion = {
            "time_of_day": meta.get("occasion_time_of_day"),
            "day_type": meta.get("occasion_day_type"),
            "session_length": meta.get("occasion_session_length"),
            "label": meta.get("occasion_label"),
        }
        return {
            "title": path.stem,
            "content": self._body_from_markdown(text),
            "function": meta.get("function"),
            "falsifier": meta.get("falsifier"),
            "confidence": meta.get("confidence"),
            "status": str(meta.get("status", "proposed")).lower(),
            "updated": meta.get("updated"),
            "occasion": occasion,
        }

    def persona_snapshot(self, occasion: Occasion | None = None) -> dict[str, Any]:
        elicited: list[dict[str, Any]] = []
        for path in sorted((self.path / "persona").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            meta = self._parse_frontmatter(text)
            if str(meta.get("layer", "")).lower() != "elicited":
                continue
            elicited.append(
                {
                    "title": path.stem,
                    "content": self._body_from_markdown(text),
                    "source": meta.get("source"),
                    "confidence": meta.get("confidence"),
                    "updated": meta.get("updated"),
                }
            )

        if occasion is None:
            return {"elicited": elicited}

        active_hypotheses: list[dict[str, Any]] = []
        for path in sorted((self.path / "hypotheses").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            payload = self._hypothesis_payload(path, text)
            status = payload["status"]
            if status in {"rejected", "retired", "superseded", "archived"}:
                continue
            if payload["occasion"]["time_of_day"] != occasion.time_of_day:
                continue
            if payload["occasion"]["day_type"] != occasion.day_type:
                continue
            if payload["occasion"]["session_length"] != occasion.session_length:
                continue
            active_hypotheses.append(payload)

        return {"elicited": elicited, "active_hypotheses": active_hypotheses}

    def hypotheses_for(self, occasion: Occasion) -> dict[str, Any]:
        ranked: list[dict[str, Any]] = []
        untested: list[dict[str, Any]] = []
        for path in sorted((self.path / "hypotheses").glob("*.md")):
            text = path.read_text(encoding="utf-8")
            payload = self._hypothesis_payload(path, text)
            status = payload["status"]
            if status in {"rejected", "retired", "superseded", "archived"}:
                continue
            if (
                payload["occasion"]["time_of_day"] == occasion.time_of_day
                and payload["occasion"]["day_type"] == occasion.day_type
                and payload["occasion"]["session_length"] == occasion.session_length
            ):
                if payload["confidence"] is None:
                    untested.append(payload)
                else:
                    ranked.append(payload)

        ranked.sort(key=lambda item: float(item.get("confidence") or -1.0), reverse=True)
        return {"occasion": occasion.as_dict(), "ranked": ranked, "untested": untested}

    def history(self, title: str, kind: str) -> list[dict[str, Any]]:
        path = self._note_path(kind, title)
        rel = path.relative_to(self.path).as_posix()
        result = self._run_git("log", "--pretty=format:%H%x1f%ct%x1f%s", "--", rel)
        if result is None or not result.stdout.strip():
            return []
        history: list[dict[str, Any]] = []
        for line in result.stdout.strip().splitlines():
            commit, timestamp, subject = line.split("\x1f", 2)
            reason = subject
            prefix = f"{kind}/{title}: "
            if reason.startswith(prefix):
                reason = reason[len(prefix) :]
            history.append(
                {
                    "commit": commit,
                    "timestamp": datetime.fromtimestamp(int(timestamp), tz=timezone.utc).isoformat(),
                    "reason": reason,
                }
            )
        return history

    def revision(self, title: str, kind: str, commit: str) -> str | None:
        path = self._note_path(kind, title)
        rel = path.relative_to(self.path).as_posix()
        result = self._run_git("show", f"{commit}:{rel}")
        if result is None:
            return None
        return result.stdout

    def export(self) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive_path = self.path.parent / f"{self.path.name}-{timestamp}.zip"
        manifest = {
            "vault": self.path.name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "files": [str(item.relative_to(self.path)).replace('\\', '/') for item in sorted(self.path.rglob("*")) if item.is_file()],
        }
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file_path in sorted(self.path.rglob("*")):
                if file_path.is_dir():
                    continue
                zf.write(file_path, arcname=file_path.relative_to(self.path).as_posix())
            zf.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
        return archive_path

    def write_work(self, title: str, content: str, *, reason: str, **meta: Any) -> Path:
        return self.write_note("works", title, content, reason=reason, **meta)

    def write_persona(self, title: str, content: str, *, reason: str, **meta: Any) -> Path:
        return self.write_note("persona", title, content, reason=reason, **meta)

    def write_hypothesis_note(self, title: str, content: str, *, reason: str, **meta: Any) -> Path:
        return self.write_note("hypotheses", title, content, reason=reason, **meta)

    def write_source(self, title: str, content: str, *, reason: str, **meta: Any) -> Path:
        return self.write_note("sources", title, content, reason=reason, **meta)

    def write_question(self, title: str, content: str, *, reason: str, **meta: Any) -> Path:
        return self.write_note("questions", title, content, reason=reason, **meta)

    def write_claim(
        self,
        title: str,
        content: str,
        *,
        reason: str,
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
            reason=reason,
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


def write_claim(vault: Vault, *, title: str, content: str, reason: str, falsifier: str | None, **kwargs: Any) -> Path:
    return vault.write_claim(title=title, content=content, reason=reason, falsifier=falsifier, **kwargs)
