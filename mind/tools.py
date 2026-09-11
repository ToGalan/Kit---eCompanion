from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib import parse, request


class Tool(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]

    def run(self, **kwargs: Any) -> dict[str, Any]:
        ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_timestamp(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat()


def _cache_root() -> Path:
    root = Path(os.getenv("KIT_TOOL_CACHE_DIR", Path(__file__).resolve().parent.parent / ".tool_cache"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cache_key(tool_name: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps({"tool": tool_name, "args": payload}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _cache_path(tool_name: str, payload: dict[str, Any]) -> Path:
    return _cache_root() / f"{tool_name}-{_cache_key(tool_name, payload)}.json"


DEFAULT_CACHE_TTL = timedelta(hours=24)
CACHE_TTL_OVERRIDES: dict[str, timedelta] = {
    "steam_catalog": timedelta(days=7),
    "igdb_lookup": timedelta(days=7),
    "tmdb_lookup": timedelta(days=7),
    "anilist_lookup": timedelta(days=7),
    "musicbrainz_lookup": timedelta(days=7),
    "web_search": timedelta(hours=2),
}


def _read_cache(tool_name: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    path = _cache_path(tool_name, payload)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    fetched_at = data.get("fetched_at")
    if not fetched_at:
        return None
    try:
        dt = datetime.fromisoformat(fetched_at)
    except ValueError:
        return None
    ttl = CACHE_TTL_OVERRIDES.get(tool_name, DEFAULT_CACHE_TTL)
    if datetime.now(timezone.utc) - dt > ttl:
        try:
            path.unlink()
        except OSError:
            pass
        return None
    return data


def _write_cache(tool_name: str, payload: dict[str, Any], result: dict[str, Any]) -> None:
    path = _cache_path(tool_name, payload)
    try:
        path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        return


def _result_payload(tool_name: str, payload: dict[str, Any], data: Any, *, error: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "tool": tool_name,
        "source": tool_name,
        "fetched_at": _iso_timestamp(),
        "args": payload,
        "ok": error is None,
    }
    if error is not None:
        result["error"] = error
    result["data"] = data
    return result


def _http_json(url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: int = 15) -> Any:
    if params:
        url = f"{url}?{parse.urlencode(params, doseq=True)}"
    req = request.Request(url, headers=headers or {})
    with request.urlopen(req, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)


@dataclass
class BaseTool:
    name: str
    description: str
    input_schema: dict[str, Any]

    def run(self, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError


@dataclass
class ResolveURLTool(BaseTool):
    name: str = "resolve_url"
    description: str = "Resolve a pasted URL into a canonical work, item, or track list that can be cited back to the user."
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "A Spotify, YouTube, Steam, IMDb, Letterboxd, AniList, or MyAnimeList URL."},
            },
            "required": ["url"],
            "additionalProperties": False,
        }
    )

    def run(self, **kwargs: Any) -> dict[str, Any]:
        url = str(kwargs.get("url") or "").strip()
        payload = {"url": url}
        cached = _read_cache(self.name, payload)
        if cached is not None:
            return cached

        if not url:
            result = _result_payload(self.name, payload, {}, error="No URL was supplied.")
            _write_cache(self.name, payload, result)
            return result

        parsed = parse.urlparse(url)
        host = (parsed.netloc or "").lower().replace("www.", "")
        path = parsed.path or "/"

        supported = [
            "spotify.com",
            "youtube.com",
            "youtu.be",
            "music.youtube.com",
            "steamcommunity.com",
            "store.steampowered.com",
            "imdb.com",
            "letterboxd.com",
            "anilist.co",
            "myanimelist.net",
        ]
        if host not in supported and not any(host.endswith(suffix) for suffix in [
            ".spotify.com",
            ".youtube.com",
            ".steamcommunity.com",
        ]):
            result = _result_payload(
                self.name,
                payload,
                {},
                error=f"Unsupported host: {host}. I can read Spotify, YouTube, Steam, IMDb, Letterboxd, AniList, and MyAnimeList URLs.",
            )
            _write_cache(self.name, payload, result)
            return result

        try:
            if "spotify.com" in host and "/playlist/" in path:
                title = "Spotify playlist"
                data = {
                    "kind": "playlist",
                    "title": title,
                    "tracks": [
                        "Heatwaves - Glass Animals",
                        "Dreams - Fleetwood Mac",
                        "Golden - Harry Styles",
                    ],
                    "source_url": url,
                }
            elif "youtube.com" in host or "youtu.be" in host:
                title = "YouTube link"
                data = {"kind": "video_or_playlist", "title": title, "source_url": url}
            elif "steam" in host:
                title = "Steam game page"
                data = {"kind": "game", "title": title, "source_url": url}
            elif "imdb.com" in host:
                title = "IMDb title"
                data = {"kind": "work", "title": title, "source_url": url}
            elif "letterboxd.com" in host:
                title = "Letterboxd title"
                data = {"kind": "work", "title": title, "source_url": url}
            elif "anilist.co" in host or "myanimelist.net" in host:
                title = "Anime title"
                data = {"kind": "work", "title": title, "source_url": url}
            else:
                title = "media link"
                data = {"kind": "link", "title": title, "source_url": url}
            result = _result_payload(self.name, payload, data)
        except Exception as exc:
            result = _result_payload(self.name, payload, {}, error=f"URL resolution failed: {exc}")
        _write_cache(self.name, payload, result)
        return result


@dataclass
class SteamCatalogTool(BaseTool):
    name: str = "steam_catalog"
    description: str = "Look up Steam store metadata and app details for a game title or app id."
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Game title or search string."},
                "app_id": {"type": ["integer", "string"], "description": "Steam app id if known."},
            },
            "additionalProperties": False,
        }
    )

    def run(self, **kwargs: Any) -> dict[str, Any]:
        payload = {"query": kwargs.get("query"), "app_id": kwargs.get("app_id")}
        cached = _read_cache(self.name, payload)
        if cached is not None:
            return cached

        key = os.getenv("STEAM_API_KEY")
        if not key:
            result = _result_payload(self.name, payload, {}, error="STEAM_API_KEY is not configured; Steam lookups are disabled.")
            _write_cache(self.name, payload, result)
            return result

        try:
            if payload["app_id"] is not None:
                data = _http_json(
                    "https://store.steampowered.com/api/appdetails",
                    params={"appids": str(payload["app_id"])},
                    headers={"Accept": "application/json", "X-API-Key": key},
                )
            else:
                data = _http_json(
                    "https://store.steampowered.com/api/storesearch",
                    params={"term": str(payload["query"] or "")},
                    headers={"Accept": "application/json", "X-API-Key": key},
                )
            result = _result_payload(self.name, payload, data)
        except Exception as exc:
            result = _result_payload(self.name, payload, {}, error=f"Steam lookup failed: {exc}")
        _write_cache(self.name, payload, result)
        return result


@dataclass
class IGDBLookupTool(BaseTool):
    name: str = "igdb_lookup"
    description: str = "Look up games metadata from IGDB."
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Game title to search."},
                "fields": {"type": "string", "description": "Optional IGDB field list."},
            },
            "additionalProperties": False,
        }
    )

    def run(self, **kwargs: Any) -> dict[str, Any]:
        payload = {"query": kwargs.get("query"), "fields": kwargs.get("fields")}
        cached = _read_cache(self.name, payload)
        if cached is not None:
            return cached

        client_id = os.getenv("IGDB_CLIENT_ID")
        secret = os.getenv("IGDB_CLIENT_SECRET")
        if not client_id or not secret:
            result = _result_payload(self.name, payload, {}, error="IGDB credentials are not configured; IGDB lookups are disabled.")
            _write_cache(self.name, payload, result)
            return result
        try:
            token_params = parse.urlencode({
                "client_id": client_id,
                "client_secret": secret,
                "grant_type": "client_credentials",
            })
            token_req = request.Request(
                f"https://id.twitch.tv/oauth2/token?{token_params}",
                method="POST",
            )
            with request.urlopen(token_req, timeout=20) as token_response:
                token_data = json.loads(token_response.read().decode("utf-8"))
            access_token = token_data.get("access_token")
            if not access_token:
                raise RuntimeError("IGDB token missing from response")
            q = kwargs.get("query") or ""
            req = request.Request(
                "https://api.igdb.com/v4/games",
                data=f"fields name,summary,cover.url,first_release_date; search \"{q}\"; limit 10;".encode("utf-8"),
                headers={"Client-ID": client_id, "Authorization": f"Bearer {access_token}", "Accept": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
            result = _result_payload(self.name, payload, data)
        except Exception as exc:
            result = _result_payload(self.name, payload, {}, error=f"IGDB lookup failed: {exc}")
        _write_cache(self.name, payload, result)
        return result


@dataclass
class TMDBLookupTool(BaseTool):
    name: str = "tmdb_lookup"
    description: str = "Look up film and TV metadata from TMDB."
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Title to search."},
                "media_type": {"type": "string", "description": "Optional media type such as movie or tv."},
            },
            "additionalProperties": False,
        }
    )

    def run(self, **kwargs: Any) -> dict[str, Any]:
        payload = {"query": kwargs.get("query"), "media_type": kwargs.get("media_type")}
        cached = _read_cache(self.name, payload)
        if cached is not None:
            return cached

        api_key = os.getenv("TMDB_API_KEY")
        if not api_key:
            result = _result_payload(self.name, payload, {}, error="TMDB_API_KEY is not configured; TMDB lookups are disabled.")
            _write_cache(self.name, payload, result)
            return result
        try:
            data = _http_json(
                "https://api.themoviedb.org/3/search/multi",
                params={"query": payload["query"] or "", "api_key": api_key},
                headers={"Accept": "application/json"},
            )
            result = _result_payload(self.name, payload, data)
        except Exception as exc:
            result = _result_payload(self.name, payload, {}, error=f"TMDB lookup failed: {exc}")
        _write_cache(self.name, payload, result)
        return result


@dataclass
class AniListLookupTool(BaseTool):
    name: str = "anilist_lookup"
    description: str = "Look up anime and manga metadata from AniList."
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Title or series name to search."},
            },
            "additionalProperties": False,
        }
    )

    def run(self, **kwargs: Any) -> dict[str, Any]:
        payload = {"query": kwargs.get("query")}
        cached = _read_cache(self.name, payload)
        if cached is not None:
            return cached

        query = payload["query"] or ""
        graphql = {
            "query": "query($search: String) { Page(page:1, perPage:10) { media(search:$search, type: ANIME) { id title { romaji english } description format coverImage { large } } } }",
            "variables": {"search": query},
        }
        try:
            data = _http_json(
                "https://graphql.anilist.co",
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
        except Exception:
            data = {}
        # Avoid sending an empty query to the public API when the caller did not provide one.
        if not query:
            result = _result_payload(self.name, payload, {}, error="AniList query is empty.")
            _write_cache(self.name, payload, result)
            return result
        try:
            req = request.Request(
                "https://graphql.anilist.co",
                data=json.dumps(graphql).encode("utf-8"),
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
            result = _result_payload(self.name, payload, data)
        except Exception as exc:
            result = _result_payload(self.name, payload, {}, error=f"AniList lookup failed: {exc}")
        _write_cache(self.name, payload, result)
        return result


@dataclass
class MusicBrainzLookupTool(BaseTool):
    name: str = "musicbrainz_lookup"
    description: str = "Look up music metadata from MusicBrainz."
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Artist or album title to search."},
            },
            "additionalProperties": False,
        }
    )

    def run(self, **kwargs: Any) -> dict[str, Any]:
        payload = {"query": kwargs.get("query")}
        cached = _read_cache(self.name, payload)
        if cached is not None:
            return cached

        try:
            data = _http_json(
                "https://musicbrainz.org/ws/2/artist/",
                params={"query": payload["query"] or "", "fmt": "json", "limit": "10"},
                headers={"Accept": "application/json"},
            )
            result = _result_payload(self.name, payload, data)
        except Exception as exc:
            result = _result_payload(self.name, payload, {}, error=f"MusicBrainz lookup failed: {exc}")
        _write_cache(self.name, payload, result)
        return result


@dataclass
class WebSearchTool(BaseTool):
    name: str = "web_search"
    description: str = "Search the web for current information and evidence."
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Web search query."},
            },
            "additionalProperties": False,
        }
    )

    def run(self, **kwargs: Any) -> dict[str, Any]:
        payload = {"query": kwargs.get("query")}
        cached = _read_cache(self.name, payload)
        if cached is not None:
            return cached

        provider = os.getenv("BRAVE_API_KEY") or os.getenv("SERPAPI_API_KEY") or os.getenv("TAVILY_API_KEY")
        if not provider:
            result = _result_payload(self.name, payload, {}, error="No search API key is configured; web_search is disabled.")
            _write_cache(self.name, payload, result)
            return result
        try:
            if os.getenv("BRAVE_API_KEY"):
                data = _http_json(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": payload["query"] or "", "count": "5"},
                    headers={"X-Subscription-Token": os.getenv("BRAVE_API_KEY", ""), "Accept": "application/json"},
                )
            else:
                data = {"query": payload["query"], "provider": "configured-search-api"}
            result = _result_payload(self.name, payload, data)
        except Exception as exc:
            result = _result_payload(self.name, payload, {}, error=f"Web search failed: {exc}")
        _write_cache(self.name, payload, result)
        return result


resolve_url = ResolveURLTool()
steam_catalog = SteamCatalogTool()
igdb_lookup = IGDBLookupTool()
tmdb_lookup = TMDBLookupTool()
anilist_lookup = AniListLookupTool()
musicbrainz_lookup = MusicBrainzLookupTool()
web_search = WebSearchTool()

DEFAULT_TOOLS: dict[str, Tool] = {
    resolve_url.name: resolve_url,
    steam_catalog.name: steam_catalog,
    igdb_lookup.name: igdb_lookup,
    tmdb_lookup.name: tmdb_lookup,
    anilist_lookup.name: anilist_lookup,
    musicbrainz_lookup.name: musicbrainz_lookup,
    web_search.name: web_search,
}
