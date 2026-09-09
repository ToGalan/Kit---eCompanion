from __future__ import annotations

import hashlib
import hmac
import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.touchpoint import Touchpoint, TouchpointStore
from kit.vault import Vault
from mind.ai import Opus5Gateway
from mind.obsidian_brain import ObsidianBrain

MEDIA_DOMAINS = {
    "youtube.com",
    "netflix.com",
    "disneyplus.com",
    "primevideo.com",
    "hbomax.com",
    "spotify.com",
    "soundcloud.com",
    "bandcamp.com",
    "steamcommunity.com",
    "store.steampowered.com",
    "letterboxd.com",
    "imdb.com",
    "trakt.tv",
    "anilist.co",
    "musicbrainz.org",
}


class BrowserEvent(BaseModel):
    domain: str
    signal: str
    timestamp: str | None = None
    timezone: str | None = None


def _token_secret() -> bytes:
    return os.environ.get("KIT_SESSION_SECRET", "kit-dev-secret").encode("utf-8")


def make_session_token(user: str) -> str:
    digest = hmac.new(_token_secret(), user.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{user}.{digest}"


def resolve_user_from_token(token: str) -> str | None:
    if not token:
        return None
    try:
        user, digest = token.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(_token_secret(), user.encode("utf-8"), hashlib.sha256).hexdigest()
    if hmac.compare_digest(digest, expected):
        return user
    return None


def get_authenticated_user(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")
    token = authorization.split(" ", 1)[1].strip()
    user = resolve_user_from_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid bearer token")
    return user


class BrowserSignalStore:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or os.environ.get("KIT_DB_PATH", "./data/kit.db"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user TEXT PRIMARY KEY,
                    timezone TEXT NOT NULL DEFAULT 'UTC'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS browser_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    signal TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    previous_domain TEXT,
                    previous_outcome TEXT,
                    session_duration_seconds REAL
                )
                """
            )

            existing_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(browser_events)").fetchall()
            }
            for column_name, column_definition in {
                "timestamp": "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'",
                "timezone": "TEXT NOT NULL DEFAULT 'UTC'",
                "previous_domain": "TEXT",
                "previous_outcome": "TEXT",
                "session_duration_seconds": "REAL",
            }.items():
                if column_name not in existing_columns:
                    conn.execute(f"ALTER TABLE browser_events ADD COLUMN {column_name} {column_definition}")

    def set_user_timezone(self, user: str, timezone_name: str) -> dict[str, Any]:
        normalized = str(timezone_name).strip() or "UTC"
        try:
            ZoneInfo(normalized)
        except Exception:
            raise ValueError(f"unsupported timezone: {normalized}")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO users (user, timezone) VALUES (?, ?) ON CONFLICT(user) DO UPDATE SET timezone = excluded.timezone",
                (user, normalized),
            )
        return {"user": user, "timezone": normalized}

    def user_timezone(self, user: str) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT timezone FROM users WHERE user = ?", (user,)).fetchone()
        return row["timezone"] if row else "UTC"

    def user_record(self, user: str) -> dict[str, Any]:
        with self._connect() as conn:
            user_row = conn.execute("SELECT timezone FROM users WHERE user = ?", (user,)).fetchone()
            rows = conn.execute(
                "SELECT domain, signal, timestamp, timezone, previous_domain, previous_outcome, session_duration_seconds FROM browser_events WHERE user = ? ORDER BY id ASC",
                (user,),
            ).fetchall()

        timezone_name = user_row["timezone"] if user_row else "UTC"
        events = [
            {
                "domain": row["domain"],
                "signal": row["signal"],
                "timestamp": row["timestamp"],
                "timezone": row["timezone"],
                "previous_domain": row["previous_domain"],
                "previous_outcome": row["previous_outcome"],
                "session_duration_seconds": row["session_duration_seconds"],
            }
            for row in rows
        ]
        return {"user": user, "timezone": timezone_name, "events": events}

    @staticmethod
    def _event_timestamp(value: str | datetime | None, *, timezone_name: str) -> tuple[datetime, str]:
        if value is None:
            dt = datetime.now(ZoneInfo(timezone_name))
        elif isinstance(value, datetime):
            dt = value
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo(timezone_name))
        else:
            try:
                dt = datetime.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"invalid timestamp: {value}") from exc
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo(timezone_name))
        dt = dt.astimezone(ZoneInfo(timezone_name))
        return dt, dt.isoformat()

    @staticmethod
    def _previous_outcome(signal: str | None) -> str | None:
        if signal == "save":
            return "completed"
        if signal == "bounce":
            return "abandoned"
        if signal == "circle":
            return "completed"
        return None

    def record(
        self,
        user: str,
        domain: str,
        signal: str,
        *,
        timestamp: str | datetime | None = None,
        timezone_name: str | None = None,
    ) -> dict[str, Any]:
        tz_name = timezone_name or self.user_timezone(user) or "UTC"
        previous = None
        with self._connect() as conn:
            previous_row = conn.execute(
                "SELECT id, domain, signal, timestamp FROM browser_events WHERE user = ? ORDER BY id DESC LIMIT 1",
                (user,),
            ).fetchone()
            if previous_row is not None:
                previous = {
                    "id": previous_row["id"],
                    "domain": previous_row["domain"],
                    "signal": previous_row["signal"],
                    "timestamp": previous_row["timestamp"],
                }

        dt, iso_ts = self._event_timestamp(timestamp, timezone_name=tz_name)
        previous_domain = previous["domain"] if previous else None
        previous_outcome = self._previous_outcome(previous["signal"]) if previous else None
        session_duration_seconds = None
        if previous and previous.get("timestamp"):
            previous_dt = datetime.fromisoformat(previous["timestamp"])
            session_duration_seconds = max(0.0, (dt - previous_dt).total_seconds())

        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO browser_events (user, domain, signal, timestamp, timezone, previous_domain, previous_outcome, session_duration_seconds) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user, domain, signal, iso_ts, tz_name, previous_domain, previous_outcome, session_duration_seconds),
            )

        self.set_user_timezone(user, tz_name)
        return {
            "id": cursor.lastrowid,
            "user": user,
            "domain": domain,
            "signal": signal,
            "timestamp": iso_ts,
            "timezone": tz_name,
            "previous_domain": previous_domain,
            "previous_outcome": previous_outcome,
            "session_duration_seconds": session_duration_seconds,
        }

    def detect_user_patterns(self, user: str) -> dict[str, dict[str, int]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT domain, signal FROM browser_events WHERE user = ? ORDER BY id ASC",
                (user,),
            ).fetchall()
        history = [{"domain": row["domain"], "signal": row["signal"]} for row in rows]
        return self._patterns_from_events(history)

    @staticmethod
    def _patterns_from_events(history: list[dict[str, str]]) -> dict[str, dict[str, int]]:
        if len(history) < 2:
            return {}

        counts: dict[str, dict[str, int]] = defaultdict(lambda: {"save": 0, "bounce": 0, "circle": 0})
        for event in history:
            domain = event["domain"]
            signal = event["signal"]
            if signal in {"save", "bounce", "circle"}:
                counts[domain][signal] += 1

        return {domain: values for domain, values in counts.items() if sum(values.values()) >= 2}

    def history_for(self, user: str) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT domain, signal, timestamp, timezone, previous_domain, previous_outcome, session_duration_seconds FROM browser_events WHERE user = ? ORDER BY id ASC",
                (user,),
            ).fetchall()
        events = [
            {
                "domain": row["domain"],
                "signal": row["signal"],
                "timestamp": row["timestamp"],
                "timezone": row["timezone"],
                "previous_domain": row["previous_domain"],
                "previous_outcome": row["previous_outcome"],
                "session_duration_seconds": row["session_duration_seconds"],
            }
            for row in rows
        ]
        return {"events": events, "patterns": self._patterns_from_events([{"domain": item["domain"], "signal": item["signal"]} for item in events]), "timezone": self.user_timezone(user)}

    def delete_for_user(self, user: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM browser_events WHERE user = ?", (user,))
            conn.execute("DELETE FROM users WHERE user = ?", (user,))

    def current_occasion(self, user: str, *, now: datetime | None = None) -> Any:
        from mind.context import current_occasion

        return current_occasion(user, now=now, store=self)


store = BrowserSignalStore()
app = FastAPI(title="Kit Browser Signal")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
brain = ObsidianBrain(vault_root=Path("vault"))
vault = Vault(Path("vault"))
touchpoint_store = TouchpointStore()


def _parse_frontmatter(content: str) -> dict[str, str]:
    if not content.startswith("---\n"):
        return {}
    try:
        end = content.index("\n---\n", 4)
    except ValueError:
        return {}
    parsed: dict[str, str] = {}
    for line in content[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key.strip()] = value.strip()
    return parsed


def _body_from_markdown(content: str) -> str:
    if content.startswith("---\n"):
        try:
            end = content.index("\n---\n", 4)
        except ValueError:
            return content.strip()
        content = content[end + 5 :]
    body = re.sub(r"(?ms)^# .*?\n+", "", content.lstrip())
    return body.strip()


def _coerce_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _domain_for_text(text: str) -> str:
    lowered = (text or "").lower()
    if any(token in lowered for token in ["spotify", "song", "album", "playlist", "music", "artist"]):
        return "music"
    if any(token in lowered for token in ["steam", "game", "controller", "quest", "save", "rpg", "playthrough"]):
        return "games"
    if any(token in lowered for token in ["film", "movie", "cinema", "screening", "director"]):
        return "film"
    if any(token in lowered for token in ["tv", "series", "show", "episode", "season", "netflix"]):
        return "tv"
    if any(token in lowered for token in ["anime", "manga", "anilist", "studio", "watchlist"]):
        return "anime"
    return "general"


def _note_id(kind: str, title: str) -> str:
    return f"{kind}:{title}"


def _wikilinks_in_text(text: str) -> list[str]:
    return re.findall(r"\[\[([^\]]+)\]\]", text or "")


def _parse_evidence(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text or text == "[]":
        return []
    matches = re.findall(r'"([^"]+)"|\'([^\']+)\'', text)
    if matches:
        values = []
        for first, second in matches:
            values.append((first or second).strip())
        return [item for item in values if item]
    return [segment.strip().strip("[]") for segment in text.split(",") if segment.strip()]


def _build_vault_graph() -> dict[str, Any]:
    directories = ["persona", "hypotheses", "works", "claims", "sources", "questions"]
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    index: dict[str, str] = {}

    for kind in directories:
        folder = vault.path / kind
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.md")):
            content = path.read_text(encoding="utf-8")
            meta = _parse_frontmatter(content)
            title = path.stem
            node = {
                "id": _note_id(kind, title),
                "kind": kind,
                "type": {
                    "persona": "persona",
                    "hypotheses": "hypothesis",
                    "works": "work",
                    "claims": "claim",
                    "sources": "source",
                    "questions": "question",
                }.get(kind, "general"),
                "label": title,
                "body": _body_from_markdown(content),
                "domain": _domain_for_text(f"{title} {meta.get('dimension') or ''} {_body_from_markdown(content)}"),
                "confidence": _coerce_float(meta.get("confidence"), default=0.0),
                "status": str(meta.get("status") or "active").lower(),
                "layer": str(meta.get("layer") or ("inferred" if kind == "hypotheses" else "elicited")).lower(),
                "path": str(path.relative_to(vault.path)),
                "occasion": None,
                "color": {
                    "persona": "#8b5cf6",
                    "hypothesis": "#38bdf8",
                    "work": "#f59e0b",
                    "claim": "#10b981",
                    "source": "#f97316",
                    "question": "#94a3b8",
                    "occasion": "#ec4899",
                }.get({
                    "persona": "persona",
                    "hypotheses": "hypothesis",
                    "works": "work",
                    "claims": "claim",
                    "sources": "source",
                    "questions": "question",
                }.get(kind, "general"), "#94a3b8"),
            }
            if kind == "hypotheses":
                occasion = {
                    "time_of_day": meta.get("occasion_time_of_day"),
                    "day_type": meta.get("occasion_day_type"),
                    "session_length": meta.get("occasion_session_length"),
                    "label": meta.get("occasion_label"),
                }
                if occasion.get("time_of_day") or occasion.get("day_type") or occasion.get("session_length"):
                    node["occasion"] = {key: value for key, value in occasion.items() if value is not None}
            if node["layer"] == "inferred" and node["status"] in {"proposed", "active"}:
                node["status"] = "untested" if node["confidence"] == 0.0 else node["status"]
            nodes.append(node)
            index[title.lower()] = _note_id(kind, title)

    occasion_seen: set[str] = set()
    for node in list(nodes):
        occasion = node.get("occasion")
        if not occasion:
            continue
        label = " | ".join(str(value) for value in [occasion.get("time_of_day"), occasion.get("day_type"), occasion.get("session_length")] if value)
        occasion_id = f"occasion:{label}"
        if occasion_id not in occasion_seen:
            occasion_seen.add(occasion_id)
            nodes.append(
                {
                    "id": occasion_id,
                    "kind": "occasion",
                    "type": "occasion",
                    "label": label,
                    "body": "Occasion context",
                    "domain": "general",
                    "confidence": 0.6,
                    "status": "known",
                    "layer": "observed",
                    "path": "occasion",
                    "occasion": occasion,
                    "color": "#ec4899",
                }
            )
        edges.append({"source": node["id"], "target": occasion_id, "kind": "occasion"})

    for node in nodes:
        body = node.get("body") or ""
        for target in _wikilinks_in_text(body):
            target_id = index.get(target.lower())
            if target_id and target_id != node["id"]:
                edges.append({"source": node["id"], "target": target_id, "kind": "wikilink"})

    for node in nodes:
        if node["kind"] != "claims":
            continue
        content = node.get("body") or ""
        meta = _parse_frontmatter(content)
        if not meta and node.get("path"):
            continue
        work_value = meta.get("work")
        if work_value:
            target_id = index.get(str(work_value).lower())
            if target_id:
                edges.append({"source": node["id"], "target": target_id, "kind": "work"})
        for target in _parse_evidence(meta.get("evidence")):
            target_id = index.get(str(target).lower())
            if target_id:
                edges.append({"source": node["id"], "target": target_id, "kind": "evidence"})

    deduped_edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for edge in edges:
        key = (str(edge["source"]), str(edge["target"]), str(edge["kind"]))
        if key in seen_edges:
            continue
        seen_edges.add(key)
        deduped_edges.append(edge)

    return {"nodes": nodes, "edges": deduped_edges}


def _related_note_paths(title: str, kind: str) -> list[Path]:
    impacted: list[Path] = []
    if title is None or not str(title).strip():
        return impacted
    normalized = str(title).strip()
    for folder in ["persona", "hypotheses", "works", "claims", "sources", "questions"]:
        folder_path = vault.path / folder
        if not folder_path.exists():
            continue
        for path in folder_path.glob("*.md"):
            content = path.read_text(encoding="utf-8")
            if path.stem.lower() == normalized.lower():
                impacted.append(path)
                continue
            if f"[[{normalized}]]" in content or f"[[{normalized.replace('_', ' ')}]]" in content:
                impacted.append(path)
            meta = _parse_frontmatter(content)
            if meta.get("work", "").lower() == normalized.lower():
                impacted.append(path)
            for item in _parse_evidence(meta.get("evidence")):
                if item.lower() == normalized.lower():
                    impacted.append(path)
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in impacted:
        if path not in seen:
            unique.append(path)
            seen.add(path)
    return unique


@app.get("/vault/graph")
def vault_graph() -> dict[str, Any]:
    graph = _build_vault_graph()
    nodes = graph["nodes"]
    edges = graph["edges"]
    return {
        "nodes": [
            {
                **node,
                "size": max(16.0, 18.0 + (node.get("confidence") or 0.0) * 22.0),
                "opacity": 0.55 + min(0.45, (node.get("confidence") or 0.0) * 0.7),
            }
            for node in nodes
        ],
        "edges": edges,
        "filters": {
            "domains": sorted({node["domain"] for node in nodes}),
            "occasions": sorted({str(node.get("occasion") or "") for node in nodes if node.get("occasion")}),
        },
    }


@app.get("/vault/note")
def get_vault_note(kind: str = Query(...), title: str = Query(...)) -> dict[str, Any]:
    target = vault.get_note(title, kind)
    if target is None:
        raise HTTPException(status_code=404, detail="note not found")
    content = target["content"]
    meta = _parse_frontmatter(content)
    body = _body_from_markdown(content)
    return {
        "title": title,
        "kind": target["kind"],
        "path": target["path"],
        "body": body,
        "frontmatter": meta,
        "history": vault.history(title, target["kind"]),
        "layer": str(meta.get("layer") or ("inferred" if target["kind"] == "hypotheses" else "elicited")).lower(),
        "confidence": _coerce_float(meta.get("confidence"), default=0.0),
    }


@app.post("/vault/note")
def update_vault_note(payload: dict[str, Any]) -> dict[str, Any]:
    kind = str(payload.get("kind") or "works").strip()
    title = str(payload.get("title") or "").strip()
    content = str(payload.get("content") or "").strip()
    if not kind or not title:
        raise HTTPException(status_code=400, detail="kind and title are required")
    written = vault.rewrite_note(title, content, kind=kind, reason="user edit")
    return {"status": "updated", "kind": kind, "title": title, "path": str(written)}


@app.delete("/vault/note")
def delete_vault_note(kind: str = Query(...), title: str = Query(...)) -> dict[str, Any]:
    path = vault._note_path(kind, title)
    if not path.exists():
        raise HTTPException(status_code=404, detail="note not found")
    path.unlink(missing_ok=True)
    vault._run_git("add", "-A")
    vault._run_git("commit", "-m", f"{kind}/{title}: user edit")
    return {"status": "deleted", "kind": kind, "title": title}


@app.post("/vault/reject")
def reject_vault_note(payload: dict[str, Any]) -> dict[str, Any]:
    kind = str(payload.get("kind") or "hypotheses").strip()
    title = str(payload.get("title") or "").strip()
    preview = bool(payload.get("preview", False))
    if not title:
        raise HTTPException(status_code=400, detail="title is required")

    impacted = [
        {
            "kind": candidate.kind,
            "title": candidate.stem,
            "path": str(candidate.relative_to(vault.path)),
        }
        for candidate in _related_note_paths(title, kind)
    ]
    if kind not in {"persona", "hypotheses", "works", "claims", "sources", "questions"}:
        raise HTTPException(status_code=400, detail="unsupported note kind")

    if preview:
        return {"status": "preview", "title": title, "kind": kind, "removed": impacted}

    target = vault._note_path(kind, title)
    if target.exists():
        target.unlink(missing_ok=True)
    for candidate in _related_note_paths(title, kind):
        if candidate.exists():
            candidate.unlink(missing_ok=True)
    vault._run_git("add", "-A")
    vault._run_git("commit", "-m", f"{kind}/{title}: user edit")
    return {"status": "rejected", "title": title, "kind": kind, "removed": impacted}


@app.get("/vault/export")
def export_vault() -> FileResponse:
    archive = vault.export()
    return FileResponse(path=str(archive), media_type="application/zip", filename=archive.name)


@app.post("/chat")
def chat(payload: dict[str, Any]) -> dict[str, Any]:
    message = str(payload.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    try:
        answer = Opus5Gateway().generate(
            "You are Kit, an evidence-first companion. Answer the user with specific, grounded reasoning and mention uncertainty when appropriate.\n\nUser: "
            + message
        )
    except Exception as exc:
        return {
            "ok": False,
            "message": "The live AI is unavailable right now. Configure ANTHROPIC_API_KEY or check the backend model connection.",
            "error": str(exc),
        }

    return {"ok": True, "message": answer.strip() or "I’m ready to answer, but I didn’t receive a model response."}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "kit"}


@app.get("/history")
def get_history(user: str = Depends(get_authenticated_user)):
    return store.history_for(user)


@app.post("/history")
def add_history(
    event: BrowserEvent,
    user: str = Depends(get_authenticated_user),
):
    if event.domain not in MEDIA_DOMAINS:
        return {"error": "domain not in allowed media list"}
    if event.signal not in {"save", "bounce", "circle"}:
        return {"error": "unsupported signal"}
    if event.timezone:
        store.set_user_timezone(user, event.timezone)
    return store.record(user, event.domain, event.signal, timestamp=event.timestamp, timezone_name=event.timezone)


@app.delete("/history")
def delete_history(user: str = Depends(get_authenticated_user)):
    store.delete_for_user(user)
    return {"deleted": True, "user": user}


@app.post("/brain/ingest")
def ingest_brain(payload: dict[str, str]):
    title = str(payload.get("title", "")).strip()
    body = str(payload.get("body", "")).strip()
    if not title or not body:
        raise HTTPException(status_code=400, detail="title and body are required")
    return brain.ingest(title, body)


@app.post("/brain/route")
def route_brain(payload: dict[str, str]):
    prompt = str(payload.get("prompt", "")).strip()
    return {"route": brain.route(prompt)}


@app.post("/touchpoint/start")
def start_touchpoint(
    payload: dict[str, Any],
    user: str = Depends(get_authenticated_user),
):
    persona_snapshot = payload.get("persona_snapshot") if isinstance(payload.get("persona_snapshot"), dict) else {}
    recent_signals = payload.get("recent_signals") if isinstance(payload.get("recent_signals"), list) else []
    days_since_contact = int(payload.get("days_since_contact", touchpoint_store.days_since_contact(user)))
    touchpoint = Touchpoint.create_for_user(
        user,
        persona_snapshot=persona_snapshot,
        recent_signals=recent_signals,
        days_since_contact=days_since_contact,
        vault=vault,
        store=touchpoint_store,
    )
    if touchpoint is None:
        return {"status": "rate_limited", "opening": None, "session_id": None}
    return {"status": "ok", "opening": touchpoint.opening, "session_id": touchpoint.session_id, "goal": touchpoint.goal}


@app.post("/touchpoint/respond")
def record_touchpoint_response(
    payload: dict[str, Any],
    user: str = Depends(get_authenticated_user),
):
    message = str(payload.get("message", "")).strip()
    session_id = str(payload.get("session_id", "")).strip() or None
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    touchpoint = Touchpoint(
        user=user,
        session_id=session_id or "session-standalone",
        goal=str(payload.get("goal") or "music"),
        days_since_contact=int(payload.get("days_since_contact", touchpoint_store.days_since_contact(user))),
        persona_snapshot=payload.get("persona_snapshot") if isinstance(payload.get("persona_snapshot"), dict) else {},
        recent_signals=payload.get("recent_signals") if isinstance(payload.get("recent_signals"), list) else [],
        vault=vault,
        store=touchpoint_store,
    )
    written = touchpoint.capture_durable_fact(message, session_id=session_id or touchpoint.session_id)
    return {"status": "stored" if written is not None else "not_durable", "session_id": touchpoint.session_id}


@app.post("/vault/claim")
def write_claim_api(payload: dict[str, Any]):
    try:
        path = vault.write_claim(
            title=str(payload.get("title", "")).strip(),
            content=str(payload.get("content", "")).strip(),
            reason=str(payload.get("reason") or "vault-api").strip() or "vault-api",
            falsifier=str(payload.get("falsifier", "")).strip(),
            dimension=payload.get("dimension"),
            confidence=payload.get("confidence"),
            work=payload.get("work"),
            evidence=payload.get("evidence") or [],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"title": path.stem, "path": str(path)}
