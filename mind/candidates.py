"""Catalogue results turned into candidates the matcher can rank.

The tools return whatever their provider returns, wrapped in `{tool, source,
fetched_at, args, ok, data}`. `match()` needs something else entirely: a title, a
source that proves the title exists, a fetch timestamp, what the work might do for
someone, and what would make recommending it wrong. Nothing bridged the two, so no
catalogue result could ever pass the verification gate -- `source` was the tool's name
rather than a URL, and the title was buried inside `data`.

This module is that bridge. Every candidate it emits carries provenance (4.2): the
provider's own page for the work, and the moment it was fetched. A row that cannot
produce a title and a URL is dropped here rather than hedged later (4.5).

Popularity is normalised to [0, 1] so the attention penalty in `match()` compares
against it meaningfully. Where a provider exposes no popularity signal the candidate
carries none: an unmeasured title is not a long-tail title, and guessing either way
would put a thumb on the scale the constitution spends 4.6 removing.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

DOMAINS = ("games", "film", "tv", "anime", "music")

# Attention is long-tailed, so raw counts are compressed rather than scaled linearly:
# popularity = count / (count + midpoint), which puts the midpoint at 0.5 and leaves
# blockbusters near 1.0 without letting one runaway value flatten everything else.
POPULARITY_MIDPOINT = {
    "tmdb": 50.0,       # TMDB's own popularity score
    "anilist": 50_000.0,  # AniList user counts
    "igdb": 500.0,      # IGDB rating counts
}

# What a work might be for, inferred from the words the provider uses to describe it.
# This is a claim about the media, not about the person (5.6), and it is deliberately
# coarse: the matcher scores the overlap, it does not trust this as a label.
_FUNCTION_HINTS = {
    "calm decompression": ("relaxing", "calm", "gentle", "cozy", "cosy", "peaceful", "soothing", "chill", "ambient", "meditative", "slow"),
    "challenge and mastery": ("difficult", "challenging", "souls-like", "roguelike", "competitive", "strategy", "puzzle", "hardcore", "skill"),
    "story immersion": ("story", "narrative", "drama", "character", "plot", "epic", "saga", "novel"),
    "social play": ("multiplayer", "co-op", "coop", "party", "online", "social", "friends"),
    "high energy": ("action", "shooter", "intense", "fast-paced", "thriller", "adrenaline", "explosive"),
    "comfort and familiarity": ("comedy", "sitcom", "wholesome", "feel-good", "nostalgic", "heartwarming", "slice of life"),
    "discovery and curiosity": ("documentary", "exploration", "experimental", "mystery", "sci-fi", "science fiction", "surreal"),
}

# The counter-function, used to state what would make the recommendation wrong (2.9).
_OPPOSING_FUNCTION = {
    "calm decompression": "you want something loud and demanding instead of a wind-down",
    "challenge and mastery": "you want something undemanding you can play half-asleep",
    "story immersion": "you want something you can dip in and out of without following a thread",
    "social play": "you want to be left alone with it",
    "high energy": "you want to come down rather than be wound up",
    "comfort and familiarity": "you want to be surprised rather than soothed",
    "discovery and curiosity": "you want something familiar rather than something that asks questions of you",
}

_DEFAULT_FUNCTION = "attention worth spending"
_DEFAULT_WRONG_IF = "this is the wrong shape for the time you actually have"


def _clean(value: Any) -> str:
    """Flatten provider text: strip markup, collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def _saturating(count: Any, midpoint: float) -> float | None:
    try:
        value = float(count)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return round(value / (value + midpoint), 4)


def _functions_for(text: str, *, extra: Iterable[str] = ()) -> list[str]:
    lowered = text.lower()
    found = [name for name, hints in _FUNCTION_HINTS.items() if any(hint in lowered for hint in hints)]
    found.extend(item for item in extra if item)
    return found or [_DEFAULT_FUNCTION]


def _wrong_if_for(functions: list[str]) -> str:
    for function in functions:
        opposing = _OPPOSING_FUNCTION.get(function)
        if opposing:
            return opposing
    return _DEFAULT_WRONG_IF


def _candidate(
    *,
    title: str,
    domain: str,
    source_url: str,
    fetched_at: str,
    description: str = "",
    popularity: float | None = None,
    extra_functions: Iterable[str] = (),
    **rest: Any,
) -> dict[str, Any] | None:
    """Assemble one candidate, or None when it cannot prove what it claims to be."""
    clean_title = _clean(title)
    url = str(source_url or "").strip()
    if not clean_title or not url.startswith(("http://", "https://")) or not fetched_at:
        return None

    functions = _functions_for(f"{clean_title} {_clean(description)}", extra=extra_functions)
    candidate: dict[str, Any] = {
        "title": clean_title,
        "domain": domain,
        "functions": functions,
        "wrong_if": _wrong_if_for(functions),
        "source": url,
        "fetched_at": fetched_at,
        "description": _clean(description)[:400],
        **rest,
    }
    if popularity is not None:
        candidate["popularity"] = popularity
    return candidate


# --------------------------------------------------------------------- providers


def from_steam(result: dict[str, Any]) -> list[dict[str, Any]]:
    data = result.get("data") or {}
    fetched_at = result.get("fetched_at")
    rows: list[dict[str, Any]] = []

    # storesearch: {"total": n, "items": [...]}
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        app_id = item.get("id")
        built = _candidate(
            title=item.get("name"),
            domain="games",
            source_url=f"https://store.steampowered.com/app/{app_id}/" if app_id else "",
            fetched_at=fetched_at,
            # Search gives a name and nothing else. Leaving the description empty is what
            # marks the row as needing a detail lookup before it can be reasoned about;
            # echoing the name back here would disguise that as content. Steam search
            # exposes no popularity figure either, so the candidate carries none.
            description="",
        )
        if built:
            rows.append(built)

    # appdetails: {"<appid>": {"success": true, "data": {...}}}
    for app_id, entry in data.items():
        if not isinstance(entry, dict) or not entry.get("success"):
            continue
        detail = entry.get("data") or {}
        genres = [str(genre.get("description")) for genre in detail.get("genres") or [] if isinstance(genre, dict)]
        categories = [str(cat.get("description")) for cat in detail.get("categories") or [] if isinstance(cat, dict)]
        built = _candidate(
            title=detail.get("name"),
            domain="games",
            source_url=f"https://store.steampowered.com/app/{detail.get('steam_appid') or app_id}/",
            fetched_at=fetched_at,
            description=" ".join([_clean(detail.get("short_description")), *genres, *categories]),
            release_date=(detail.get("release_date") or {}).get("date"),
            # A delisted or unreleased title is filtered before ranking (4.4).
            purchase_status="unavailable" if (detail.get("release_date") or {}).get("coming_soon") else "available",
        )
        if built:
            rows.append(built)
    return rows


def from_tmdb(result: dict[str, Any]) -> list[dict[str, Any]]:
    fetched_at = result.get("fetched_at")
    rows: list[dict[str, Any]] = []
    for item in (result.get("data") or {}).get("results") or []:
        if not isinstance(item, dict):
            continue
        media_type = str(item.get("media_type") or "").lower()
        if media_type not in {"movie", "tv"}:
            continue
        built = _candidate(
            title=item.get("title") or item.get("name"),
            domain="film" if media_type == "movie" else "tv",
            source_url=f"https://www.themoviedb.org/{media_type}/{item.get('id')}" if item.get("id") else "",
            fetched_at=fetched_at,
            description=item.get("overview"),
            popularity=_saturating(item.get("popularity"), POPULARITY_MIDPOINT["tmdb"]),
            release_date=item.get("release_date") or item.get("first_air_date"),
        )
        if built:
            rows.append(built)
    return rows


def from_anilist(result: dict[str, Any]) -> list[dict[str, Any]]:
    fetched_at = result.get("fetched_at")
    page = ((result.get("data") or {}).get("data") or {}).get("Page") or {}
    rows: list[dict[str, Any]] = []
    for item in page.get("media") or []:
        if not isinstance(item, dict):
            continue
        titles = item.get("title") or {}
        built = _candidate(
            title=titles.get("english") or titles.get("romaji"),
            domain="anime",
            source_url=f"https://anilist.co/anime/{item.get('id')}" if item.get("id") else "",
            fetched_at=fetched_at,
            description=item.get("description"),
            popularity=_saturating(item.get("popularity"), POPULARITY_MIDPOINT["anilist"]),
            extra_functions=[_clean(genre).lower() for genre in item.get("genres") or []],
        )
        if built:
            rows.append(built)
    return rows


def from_musicbrainz(result: dict[str, Any]) -> list[dict[str, Any]]:
    data = result.get("data") or {}
    fetched_at = result.get("fetched_at")
    rows: list[dict[str, Any]] = []

    # Release groups are works; artists are not something to put on tonight.
    for group in data.get("release-groups") or []:
        if not isinstance(group, dict):
            continue
        credits = group.get("artist-credit") or []
        artist = _clean((credits[0] or {}).get("name")) if credits and isinstance(credits[0], dict) else ""
        title = _clean(group.get("title"))
        built = _candidate(
            title=f"{title} — {artist}" if artist else title,
            domain="music",
            source_url=f"https://musicbrainz.org/release-group/{group.get('id')}" if group.get("id") else "",
            fetched_at=fetched_at,
            description=" ".join(str(part) for part in [group.get("primary-type"), *(group.get("secondary-types") or [])] if part),
        )
        if built:
            rows.append(built)
    return rows


def from_web_search(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Context only.

    4.1 makes web search a supplement and never the authority on whether a title
    exists, so these are marked and can be excluded before ranking.
    """
    data = result.get("data") or {}
    fetched_at = result.get("fetched_at")
    entries = data.get("results") or data.get("web", {}).get("results") or []
    rows: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        built = _candidate(
            title=item.get("title"),
            domain="general",
            source_url=item.get("url") or item.get("link") or "",
            fetched_at=fetched_at,
            description=item.get("description") or item.get("snippet"),
            authoritative=False,
        )
        if built:
            rows.append(built)
    return rows


ADAPTERS: dict[str, Callable[[dict[str, Any]], list[dict[str, Any]]]] = {
    "steam_catalog": from_steam,
    "tmdb_lookup": from_tmdb,
    "anilist_lookup": from_anilist,
    "musicbrainz_lookup": from_musicbrainz,
    "web_search": from_web_search,
}

# Which tool answers for which domain. IGDB has no adapter yet, so games rest on Steam.
DOMAIN_TOOLS = {
    "games": ("steam_catalog",),
    "film": ("tmdb_lookup",),
    "tv": ("tmdb_lookup",),
    "anime": ("anilist_lookup",),
    "music": ("musicbrainz_lookup",),
}


def to_candidates(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Adapt one tool result. A failed lookup yields nothing, never a placeholder."""
    if not isinstance(result, dict) or not result.get("ok"):
        return []
    adapter = ADAPTERS.get(str(result.get("tool") or ""))
    if adapter is None:
        return []
    try:
        return adapter(result)
    except Exception:
        # A provider changing its response shape degrades that domain; it does not
        # fail the turn (4.7).
        logger.warning("could not adapt a %s result", result.get("tool"), exc_info=True)
        return []


# Storefront search returns a name and nothing else, so there is no text to reason
# about what the work is for -- and a candidate with no function scores zero against
# every persona. Detail lookups fill that in, bounded because each one is a request.
ENRICH_LIMIT = 3


def _steam_app_id(candidate: dict[str, Any]) -> str | None:
    found = re.search(r"/app/(\d+)/", str(candidate.get("source") or ""))
    return found.group(1) if found else None


def _enrich(rows: list[dict[str, Any]], *, registry: dict[str, Any]) -> list[dict[str, Any]]:
    """Fill in missing descriptions for the first few games, leaving the rest as they are."""
    tool = registry.get("steam_catalog")
    if tool is None:
        return rows

    enriched: list[dict[str, Any]] = []
    spent = 0
    for row in rows:
        app_id = _steam_app_id(row) if row.get("domain") == "games" and not row.get("description") else None
        if app_id is None or spent >= ENRICH_LIMIT:
            enriched.append(row)
            continue
        spent += 1
        try:
            detail = to_candidates(tool.run(app_id=app_id))
        except Exception:
            logger.warning("could not enrich steam app %s", app_id, exc_info=True)
            detail = []
        enriched.append(detail[0] if detail else row)
    return enriched


def gather(
    query: str,
    *,
    domains: Iterable[str] | None = None,
    tools: dict[str, Any] | None = None,
    include_web: bool = False,
) -> dict[str, Any]:
    """Search the authoritative source for each domain and return what verified.

    Returns the candidates alongside the domains that could not be reached, because a
    tool that fails removes its domain from the request rather than failing the request
    or being quietly papered over (4.7) -- and Kit has to be able to say which.
    """
    from mind.tools import DEFAULT_TOOLS

    registry = tools if tools is not None else DEFAULT_TOOLS
    wanted = [domain for domain in (domains or DOMAINS) if domain in DOMAIN_TOOLS]
    text = _clean(query)

    candidates: list[dict[str, Any]] = []
    unavailable: dict[str, str] = {}
    if not text:
        return {"candidates": [], "unavailable": {}, "query": text}

    attempted: set[str] = set()
    for domain in wanted:
        for tool_name in DOMAIN_TOOLS[domain]:
            tool = registry.get(tool_name)
            if tool is None:
                unavailable[domain] = f"{tool_name} is not available"
                continue
            if tool_name in attempted:
                continue
            attempted.add(tool_name)
            try:
                result = tool.run(query=text)
            except Exception as exc:
                unavailable[domain] = f"{tool_name} failed: {exc}"
                continue
            if not result.get("ok"):
                unavailable[domain] = str(result.get("error") or f"{tool_name} returned no data")
                continue
            adapted = to_candidates(result)
            if not adapted:
                unavailable[domain] = f"{tool_name} returned nothing usable"
            candidates.extend(adapted)

    candidates = _enrich(candidates, registry=registry)

    if include_web:
        web = registry.get("web_search")
        if web is not None:
            try:
                candidates.extend(to_candidates(web.run(query=text)))
            except Exception:
                logger.warning("web search failed", exc_info=True)

    return {"candidates": candidates, "unavailable": unavailable, "query": text}
