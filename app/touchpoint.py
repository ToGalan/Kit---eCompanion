from __future__ import annotations

import os
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from kit.vault import Vault

MEDIA_DIMENSIONS = {
    "music": {
        "keywords": [
            "music",
            "song",
            "album",
            "playlist",
            "artist",
            "spotify",
            "soundcloud",
            "bandcamp",
            "melody",
            "genre",
            "listen",
            "listening",
            "jazz",
            "rock",
            "indie",
            "pop",
        ],
        "domains": ["spotify.com", "soundcloud.com", "bandcamp.com", "musicbrainz.org"],
    },
    "games": {
        "keywords": [
            "game",
            "games",
            "steam",
            "controller",
            "playthrough",
            "rpg",
            "platformer",
            "co-op",
            "achievement",
            "gaming",
            "demo",
            "save",
            "campaign",
        ],
        "domains": ["steamcommunity.com", "store.steampowered.com"],
    },
    "film": {
        "keywords": [
            "film",
            "movie",
            "cinema",
            "screening",
            "director",
            "watch movie",
            "movie night",
            "letterboxd",
            "imdb",
        ],
        "domains": ["letterboxd.com", "imdb.com"],
    },
    "tv": {
        "keywords": [
            "tv",
            "series",
            "show",
            "episode",
            "season",
            "netflix",
            "primevideo",
            "hbomax",
            "trakt",
            "binge",
            "showing",
        ],
        "domains": ["netflix.com", "primevideo.com", "hbomax.com", "trakt.tv"],
    },
    "anime": {
        "keywords": [
            "anime",
            "manga",
            "anilist",
            "watchlist",
            "episode",
            "season",
            "studio",
            "cartoon",
            "otaku",
        ],
        "domains": ["anilist.co"],
    },
}


class TouchpointStore:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or os.environ.get("KIT_TOUCHPOINT_DB_PATH", "./data/touchpoints.db"))
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
                CREATE TABLE IF NOT EXISTS touchpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user TEXT NOT NULL,
                    session_id TEXT NOT NULL UNIQUE,
                    goal TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    days_since_contact INTEGER NOT NULL
                )
                """
            )

    def has_started_today(self, user: str, *, today: date | None = None) -> bool:
        today = today or datetime.now(timezone.utc).date()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM touchpoints WHERE user = ? AND date(created_at) = ? LIMIT 1",
                (user, today.isoformat()),
            ).fetchone()
        return row is not None

    def record_touchpoint(
        self, *, user: str, session_id: str, goal: str, days_since_contact: int, started_at: datetime | None = None
    ) -> bool:
        started_at = started_at or datetime.now(timezone.utc)
        if self.has_started_today(user, today=started_at.date()):
            return False
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO touchpoints (user, session_id, goal, created_at, days_since_contact) VALUES (?, ?, ?, ?, ?)",
                (user, session_id, goal, started_at.isoformat(), int(days_since_contact)),
            )
        return True

    def last_touchpoint_for(self, user: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user, session_id, goal, created_at, days_since_contact FROM touchpoints WHERE user = ? ORDER BY id DESC LIMIT 1",
                (user,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def days_since_contact(self, user: str, *, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        row = self.last_touchpoint_for(user)
        if row is None:
            return 999
        created_at = datetime.fromisoformat(row["created_at"])
        delta = now - created_at
        return max(0, int(delta.days))


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _keyword_score(text: str, keywords: list[str]) -> int:
    lowered = text.lower()
    return sum(1 for keyword in keywords if keyword.lower() in lowered)


def _sensor_score_for_dimension(persona_snapshot: dict[str, Any], recent_signals: list[dict[str, Any]] | None, dimension: str) -> int:
    score = 0
    for fact in persona_snapshot.get("elicited", []) or []:
        content = f"{fact.get('title', '')} {fact.get('content', '')}".lower()
        score += _keyword_score(content, MEDIA_DIMENSIONS[dimension]["keywords"])
    for signal in recent_signals or []:
        domain = str(signal.get("domain", "")).lower()
        if domain in MEDIA_DIMENSIONS[dimension]["domains"]:
            score += 3
        else:
            signal_text = f"{signal.get('signal', '')} {signal.get('domain', '')}".lower()
            score += _keyword_score(signal_text, MEDIA_DIMENSIONS[dimension]["keywords"])
    return score


def _dimension_labels() -> list[str]:
    return ["music", "games", "film", "tv", "anime"]


def _pick_weakest_dimension(persona_snapshot: dict[str, Any], recent_signals: list[dict[str, Any]] | None) -> str:
    scores = {
        dimension: _sensor_score_for_dimension(persona_snapshot, recent_signals, dimension)
        for dimension in _dimension_labels()
    }
    if not any(scores.values()):
        return "music"
    return min(scores, key=lambda dimension: (scores[dimension], _dimension_labels().index(dimension)))


def _goal_for_dimension(dimension: str) -> str:
    prompts = {
        "music": "music has been in rotation lately",
        "games": "games have been on your radar lately",
        "film": "films or movie nights that have been standing out lately",
        "tv": "TV shows or series you have been thinking about lately",
        "anime": "anime or manga that has been worth a quick mention lately",
    }
    return prompts.get(dimension, "media habits that have been most on your mind lately")


@dataclass
class Touchpoint:
    user: str
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    goal: str = "music"
    days_since_contact: int = 0
    persona_snapshot: dict[str, Any] = field(default_factory=dict)
    recent_signals: list[dict[str, Any]] = field(default_factory=list)
    vault: Vault | None = None
    store: TouchpointStore | None = None

    @classmethod
    def create_for_user(
        cls,
        user: str,
        *,
        persona_snapshot: dict[str, Any] | None = None,
        recent_signals: list[dict[str, Any]] | None = None,
        days_since_contact: int = 0,
        vault: Vault | None = None,
        store: TouchpointStore | None = None,
        session_id: str | None = None,
    ) -> "Touchpoint | None":
        store = store or TouchpointStore()
        goal = _goal_for_dimension(_pick_weakest_dimension(persona_snapshot or {}, recent_signals or []))
        session_id = session_id or uuid.uuid4().hex
        if not store.record_touchpoint(
            user=user,
            session_id=session_id,
            goal=goal,
            days_since_contact=days_since_contact,
        ):
            return None
        return cls(
            user=user,
            session_id=session_id,
            goal=goal,
            days_since_contact=days_since_contact,
            persona_snapshot=persona_snapshot or {},
            recent_signals=recent_signals or [],
            vault=vault or Vault(Path("vault")),
            store=store,
        )

    @classmethod
    def start_for_user(cls, *args: Any, **kwargs: Any) -> "Touchpoint | None":
        return cls.create_for_user(*args, **kwargs)

    @property
    def opening(self) -> str:
        gap_text = ""
        if self.days_since_contact >= 7:
            gap_text = f"It’s been {self.days_since_contact} days since we last checked in, so I wanted to reconnect with a quick question."
        elif self.days_since_contact >= 2:
            gap_text = f"It’s been a little while since we last checked in, so I wanted to pick up where we left off."
        else:
            gap_text = "I wanted to check in with one quick question."

        from mind.elicitation import generate_session_prompt

        prompt = generate_session_prompt(self.persona_snapshot, occasion=self.persona_snapshot.get("active_hypotheses", [{}])[0].get("occasion") if self.persona_snapshot.get("active_hypotheses") else None)
        if self.goal:
            return f"{gap_text} {self.goal}. {prompt}"
        return f"{gap_text} {prompt}"

    @staticmethod
    def is_durable_fact(text: str) -> bool:
        cleaned = _normalize_text(text)
        if len(cleaned.split()) < 6:
            return False
        durable_patterns = [
            r"\b(i (like|love|prefer|avoid|enjoy|am into|keep coming back to|habitually|mostly|usually)\b)",
            r"\b(i (listen to|watch|play|read|seek out|return to)\b)",
            r"\b(i'm into|i am into|i tend to|i usually|i mostly)\b",
        ]
        return any(re.search(pattern, cleaned, flags=re.IGNORECASE) for pattern in durable_patterns)

    def capture_durable_fact(self, user_message: str, *, session_id: str | None = None) -> Path | None:
        message = _normalize_text(user_message)
        if not self.is_durable_fact(message):
            return None
        vault = self.vault or Vault(Path("vault"))
        title = message[:80].strip().rstrip(".") or "User fact"
        reason = (session_id or self.session_id) or "touchpoint"
        return vault.write_persona_fact(
            title=title,
            content=message,
            reason=reason,
            layer="elicited",
            source="touchpoint",
            confidence=0.8,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "user": self.user,
            "session_id": self.session_id,
            "goal": self.goal,
            "days_since_contact": self.days_since_contact,
            "opening": self.opening,
        }


store = TouchpointStore()


def _default_touchpoint_store() -> TouchpointStore:
    return store


def _default_vault() -> Vault:
    return Vault(Path("vault"))


def _days_since_last_contact(user: str, store: TouchpointStore | None = None) -> int:
    store = store or _default_touchpoint_store()
    return store.days_since_contact(user)
