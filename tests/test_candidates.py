from __future__ import annotations

from typing import Any

from mind import candidates
from mind.freshness import verify_candidate
from mind.matching import match

FETCHED = "2026-09-14T10:00:00+00:00"


def wrap(tool: str, data: Any, *, ok: bool = True) -> dict[str, Any]:
    """The envelope every tool returns."""
    return {"tool": tool, "source": tool, "fetched_at": FETCHED, "args": {}, "ok": ok, "data": data}


def test_steam_search_becomes_verifiable_game_candidates():
    result = wrap("steam_catalog", {"total": 1, "items": [{"id": 1145360, "name": "Hades", "type": "app"}]})

    rows = candidates.to_candidates(result)

    assert len(rows) == 1
    assert rows[0]["title"] == "Hades"
    assert rows[0]["domain"] == "games"
    assert rows[0]["source"] == "https://store.steampowered.com/app/1145360/"
    # The gate that nothing could pass before: a title plus a real source and timestamp.
    assert verify_candidate(rows[0]) is True


def test_steam_appdetails_carry_genres_and_release_state():
    result = wrap(
        "steam_catalog",
        {
            "1145360": {
                "success": True,
                "data": {
                    "steam_appid": 1145360,
                    "name": "Hades",
                    "short_description": "A rogue-like dungeon crawler.",
                    "genres": [{"description": "Action"}, {"description": "Roguelike"}],
                    "release_date": {"coming_soon": False, "date": "17 Sep, 2020"},
                },
            }
        },
    )

    row = candidates.to_candidates(result)[0]

    assert row["title"] == "Hades"
    assert "challenge and mastery" in row["functions"]
    assert verify_candidate(row) is True


def test_an_unreleased_game_is_filtered_before_ranking():
    result = wrap(
        "steam_catalog",
        {
            "9": {
                "success": True,
                "data": {
                    "steam_appid": 9,
                    "name": "Not Out Yet",
                    "short_description": "A calm game.",
                    "release_date": {"coming_soon": True, "date": "2027"},
                },
            }
        },
    )

    row = candidates.to_candidates(result)[0]

    # 4.4: a title the person cannot reach does not get ranked and caveated.
    assert verify_candidate(row) is False


def test_tmdb_results_split_film_from_tv_and_normalise_popularity():
    result = wrap(
        "tmdb_lookup",
        {
            "results": [
                {"id": 603, "media_type": "movie", "title": "The Matrix", "overview": "An action thriller.", "popularity": 50.0},
                {"id": 1396, "media_type": "tv", "name": "Breaking Bad", "overview": "A slow drama.", "popularity": 150.0},
                {"id": 7, "media_type": "person", "name": "A Director"},
            ]
        },
    )

    rows = candidates.to_candidates(result)

    assert [row["domain"] for row in rows] == ["film", "tv"]
    assert rows[0]["source"] == "https://www.themoviedb.org/movie/603"
    # Compressed into [0, 1] so the attention penalty in match() is comparable to it.
    assert rows[0]["popularity"] == 0.5
    assert 0.0 < rows[1]["popularity"] < 1.0
    assert all(verify_candidate(row) for row in rows)


def test_anilist_media_become_anime_candidates():
    result = wrap(
        "anilist_lookup",
        {
            "data": {
                "Page": {
                    "media": [
                        {
                            "id": 21,
                            "title": {"english": "One Piece", "romaji": "One Piece"},
                            "description": "<p>A <i>long</i> adventure.</p>",
                            "genres": ["Adventure"],
                            "popularity": 50_000,
                        }
                    ]
                }
            }
        },
    )

    row = candidates.to_candidates(result)[0]

    assert row["title"] == "One Piece"
    assert row["source"] == "https://anilist.co/anime/21"
    # Provider markup does not reach the user.
    assert "<" not in row["description"]
    assert row["popularity"] == 0.5
    assert verify_candidate(row) is True


def test_musicbrainz_release_groups_are_credited_works():
    result = wrap(
        "musicbrainz_lookup",
        {
            "release-groups": [
                {
                    "id": "0a1b",
                    "title": "For Emma, Forever Ago",
                    "primary-type": "Album",
                    "artist-credit": [{"name": "Bon Iver"}],
                }
            ]
        },
    )

    row = candidates.to_candidates(result)[0]

    assert row["title"] == "For Emma, Forever Ago — Bon Iver"
    assert row["domain"] == "music"
    assert row["source"] == "https://musicbrainz.org/release-group/0a1b"
    assert verify_candidate(row) is True


def test_a_row_that_cannot_prove_itself_is_dropped():
    # No id, so no provider page, so no way to show the title exists (4.5).
    result = wrap("steam_catalog", {"items": [{"name": "Ghost Game"}]})
    assert candidates.to_candidates(result) == []

    # A failed lookup yields nothing rather than a placeholder.
    assert candidates.to_candidates(wrap("tmdb_lookup", {}, ok=False)) == []
    assert candidates.to_candidates(None) == []


def test_every_candidate_states_what_would_make_it_wrong():
    result = wrap(
        "steam_catalog",
        {"items": [{"id": 1, "name": "A Calm Cozy Farming Game"}, {"id": 2, "name": "Intense Action Shooter"}]},
    )

    rows = candidates.to_candidates(result)

    # 2.9: every recommendation carries its own falsifier, and it is specific to the
    # function the work serves rather than one generic line.
    assert all(row["wrong_if"] for row in rows)
    assert rows[0]["wrong_if"] != rows[1]["wrong_if"]


class _StubTool:
    def __init__(self, name: str, result: Any = None, *, raises: bool = False):
        self.name = name
        self._result = result
        self._raises = raises

    def run(self, **_kwargs: Any) -> dict[str, Any]:
        if self._raises:
            raise RuntimeError("provider unreachable")
        return self._result


def test_gather_reports_the_domains_it_could_not_reach():
    tools = {
        "steam_catalog": _StubTool("steam_catalog", wrap("steam_catalog", {"items": [{"id": 1, "name": "Hades"}]})),
        "tmdb_lookup": _StubTool("tmdb_lookup", wrap("tmdb_lookup", {}, ok=False) | {"error": "TMDB_API_KEY is not configured"}),
        "anilist_lookup": _StubTool("anilist_lookup", None, raises=True),
        "musicbrainz_lookup": _StubTool("musicbrainz_lookup", wrap("musicbrainz_lookup", {"release-groups": []})),
    }

    found = candidates.gather("something short tonight", tools=tools)

    assert [row["title"] for row in found["candidates"]] == ["Hades"]
    # 4.7: a failing tool removes its domain from the request and says so, rather than
    # failing the turn or letting Kit guess in its place.
    assert "TMDB_API_KEY" in found["unavailable"]["film"]
    assert "provider unreachable" in found["unavailable"]["anime"]
    assert found["unavailable"]["music"]
    assert "games" not in found["unavailable"]


def test_gathered_candidates_survive_ranking():
    """The whole point: a real catalogue row reaching a ranked offer."""
    tools = {
        "steam_catalog": _StubTool(
            "steam_catalog",
            wrap(
                "steam_catalog",
                {
                    "items": [
                        {"id": 1, "name": "A Calm Cozy Fishing Game"},
                        {"id": 2, "name": "Relentless Competitive Shooter"},
                    ]
                },
            ),
        )
    }
    persona = {
        "elicited": [{"title": "After-work reset", "content": "I want calm low-stimulation things that help me decompress."}],
        "active_hypotheses": [{"function": "calm decompression"}],
    }

    found = candidates.gather("something for tonight", domains=["games"], tools=tools)
    ranked = match(persona, found["candidates"], k=3)

    assert ranked, "verified catalogue candidates should reach the ranking"
    assert ranked[0].title == "A Calm Cozy Fishing Game"
    assert ranked[0].wrong_if
