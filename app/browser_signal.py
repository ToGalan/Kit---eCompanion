from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from kit.vault import Vault
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


class BrowserSignalStore:
    def __init__(self):
        self.events: dict[str, list[dict[str, str]]] = defaultdict(list)

    def record(self, user: str, domain: str, signal: str) -> dict[str, Any]:
        event = {"domain": domain, "signal": signal}
        self.events[user].append(event)
        return event

    def detect_user_patterns(self, user: str) -> dict[str, dict[str, int]]:
        history = self.events.get(user, [])
        if len(history) < 2:
            return {}

        counts: dict[str, dict[str, int]] = defaultdict(lambda: {"save": 0, "bounce": 0, "circle": 0})
        for event in history:
            domain = event["domain"]
            signal = event["signal"]
            if signal in {"save", "bounce", "circle"}:
                counts[domain][signal] += 1

        # A single visit is never evidence; a pattern requires repeated signals over time.
        return {domain: values for domain, values in counts.items() if sum(values.values()) >= 2}

    def history_for(self, user: str) -> dict[str, Any]:
        return {"events": self.events.get(user, []), "patterns": self.detect_user_patterns(user)}

    def delete_for_user(self, user: str) -> None:
        self.events.pop(user, None)


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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "kit"}


@app.get("/history/{user}")
def get_history(user: str):
    return store.history_for(user)


@app.post("/history/{user}")
def add_history(user: str, event: BrowserEvent):
    if event.domain not in MEDIA_DOMAINS:
        return {"error": "domain not in allowed media list"}
    if event.signal not in {"save", "bounce", "circle"}:
        return {"error": "unsupported signal"}
    return store.record(user, event.domain, event.signal)


@app.delete("/history/{user}")
def delete_history(user: str):
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


@app.post("/vault/claim")
def write_claim_api(payload: dict[str, Any]):
    try:
        path = vault.write_claim(
            title=str(payload.get("title", "")).strip(),
            content=str(payload.get("content", "")).strip(),
            falsifier=str(payload.get("falsifier", "")).strip(),
            dimension=payload.get("dimension"),
            confidence=payload.get("confidence"),
            work=payload.get("work"),
            evidence=payload.get("evidence") or [],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"title": path.stem, "path": str(path)}
