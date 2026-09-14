"""Turning a turn in the conversation into verified, ranked candidates.

This is the automation between the catalogues and the answer. When the person is
asking for something to watch, play or listen to, Kit searches the authoritative
source for each domain in play, drops anything it cannot verify or they cannot reach,
ranks what survives against the persona, and hands the model a shortlist.

The model writes the offer; it does not choose the title. That split is deliberate:
4.5 makes fabrication the worst failure this product can have, so the set of titles
that may be mentioned is decided by what a provider confirmed, not by recall. When
nothing verifies, the shortlist is empty and saying so is the correct answer (8.2).
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from kit.vault import Occasion
from mind.candidates import DOMAINS, gather
from mind.matching import match

# How many verified titles the model is shown. It may offer one (2.4); the rest are
# there so it can pick the one that fits rather than the one that happened to rank.
SHORTLIST_SIZE = 5

_DOMAIN_WORDS = {
    "games": ("game", "games", "play", "playing", "roguelike", "rpg", "shooter", "steam", "console", "controller"),
    "film": ("film", "films", "movie", "movies", "cinema", "watch a film"),
    "tv": ("tv", "series", "show", "shows", "episode", "season", "binge"),
    "anime": ("anime", "manga", "shounen", "seinen", "studio ghibli"),
    "music": ("music", "album", "albums", "listen", "listening", "song", "songs", "record", "playlist"),
}

_ASKING = (
    r"\b(what should i|what can i|any(thing)? (good|else)|recommend|suggestion|suggest|got anything|give me something)\b",
    r"\b(i want something|looking for something|in the mood for|what do you think i)\b",
    r"\b(what's worth|whats worth|worth (watching|playing|listening))\b",
)

# Function language is not catalogue language. These are the words a storefront search
# actually matches, mapped from the functions the persona is described in.
_FUNCTION_SEARCH_TERMS = {
    "cozy relaxing": ("calm", "quiet", "decompress", "recovery", "rest", "wind down", "cozy", "cosy", "relaxing", "chill", "low-stimulation", "gentle"),
    "challenging difficult": ("challenge", "challenging", "mastery", "difficult", "hard", "punishing", "skill"),
    "immersive": ("focus", "immersive", "absorbing", "concentrate"),
    "story rich narrative": ("story", "narrative", "plot", "character", "story-driven"),
    "co-op multiplayer": ("social", "co-op", "coop", "multiplayer", "with friends", "together"),
    "fast paced action": ("energy", "energetic", "fast", "intense", "action", "adrenaline"),
    "wholesome feel good": ("comfort", "comforting", "wholesome", "feel good", "light", "funny", "warm"),
    "exploration": ("discovery", "explore", "exploration", "curious", "new"),
}


def _normalize(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def wants_a_recommendation(text: str) -> bool:
    lowered = _normalize(text).lower()
    if not lowered:
        return False
    return any(re.search(pattern, lowered) for pattern in _ASKING)


def domains_in(text: str) -> list[str]:
    """Which domains the person actually named. Empty means they did not narrow it."""
    lowered = _normalize(text).lower()
    return [domain for domain, words in _DOMAIN_WORDS.items() if any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in words)]


def _persona_text(persona_snapshot: dict[str, Any] | None) -> str:
    snapshot = persona_snapshot or {}
    parts: list[str] = []
    for fact in snapshot.get("elicited", []) or []:
        if isinstance(fact, dict):
            parts.append(f"{fact.get('title', '')} {fact.get('content', '')}")
    for item in snapshot.get("active_hypotheses", []) or []:
        if isinstance(item, dict):
            parts.append(str(item.get("function") or ""))
    return _normalize(" ".join(parts)).lower()


def search_terms(text: str, persona_snapshot: dict[str, Any] | None) -> str:
    """What to actually send a catalogue.

    The raw message is the wrong query: "what should I watch this weekend" matches a
    band called What Is, which is how a conversational string scores 100 against an
    artist index. A named title wins; otherwise the persona's function language is
    translated into words a storefront index contains.
    """
    message = _normalize(text)
    quoted = re.findall(r"[\"“']([^\"”']{2,60})[\"”']", message)
    if quoted:
        return _normalize(quoted[0])

    haystack = f"{_persona_text(persona_snapshot)} {message.lower()}"
    terms: list[str] = []
    for catalogue_words, markers in _FUNCTION_SEARCH_TERMS.items():
        if any(marker in haystack for marker in markers) and catalogue_words not in terms:
            terms.append(catalogue_words)
    return " ".join(terms[:2])


def shortlist(
    persona_snapshot: dict[str, Any] | None,
    message: str,
    *,
    occasion: Occasion | None = None,
    domains: Iterable[str] | None = None,
    tools: dict[str, Any] | None = None,
    user_services: dict[str, Any] | None = None,
    region: str | None = None,
    limit: int = SHORTLIST_SIZE,
) -> dict[str, Any]:
    """Verified, ranked candidates for this turn, with what could not be reached."""
    wanted = list(domains) if domains else (domains_in(message) or list(DOMAINS))
    terms = search_terms(message, persona_snapshot)
    if not terms:
        return {"matches": [], "unavailable": {}, "terms": "", "domains": wanted}

    found = gather(terms, domains=wanted, tools=tools)
    ranked = match(
        persona_snapshot or {},
        found["candidates"],
        k=limit,
        user_services=user_services,
        region=region,
    )
    by_title = {row["title"]: row for row in found["candidates"]}
    matches = [
        {
            "title": item.title,
            "function_served": item.function_served,
            "confidence": item.confidence,
            "wrong_if": item.wrong_if,
            "domain": by_title.get(item.title, {}).get("domain", "general"),
            "source": by_title.get(item.title, {}).get("source", ""),
        }
        for item in ranked
    ]
    return {"matches": matches, "unavailable": found["unavailable"], "terms": terms, "domains": wanted}


def shortlist_block(found: dict[str, Any]) -> str:
    """The shortlist, rendered for the system prompt."""
    lines: list[str] = []
    matches = found.get("matches") or []

    if matches:
        lines.append(
            "Titles confirmed by an authoritative source just now, ranked by how well they fit "
            "this person. You may name these and nothing else. Offer at most one, say why it "
            "fits from what you know about them, and give the wrong_if as your own honest caveat:"
        )
        for item in matches:
            lines.append(
                f"- {item['title']} [{item['domain']}] serves {item['function_served']} "
                f"(fit {item['confidence']}) — wrong if {item['wrong_if']} — {item['source']}"
            )
    else:
        lines.append(
            "No title could be confirmed for this turn. Say you do not have something worth "
            "recommending yet and ask one concrete question about a recent episode. Do not name "
            "a title from memory: an unverified title is dropped, never hedged."
        )

    unavailable = found.get("unavailable") or {}
    if unavailable:
        reachable = ", ".join(sorted(unavailable))
        lines.append(
            f"These domains could not be searched this turn: {reachable}. "
            "If it matters to the answer, say plainly that you could not check them."
        )

    return "\n".join(lines)
