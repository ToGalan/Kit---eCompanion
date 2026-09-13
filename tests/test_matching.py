
from kit.vault import Occasion, Vault
from mind.matching import Match, match


# A real candidate arrives from an authoritative source and carries its provenance
# (4.2). Fixtures say so explicitly, because match() now refuses anything that cannot
# prove it exists (4.5) and these tests are about ranking, not about that gate.
def sourced(candidate: dict) -> dict:
    return {
        "source": f"https://api.themoviedb.org/3/find/{candidate['title'].replace(' ', '-').lower()}",
        "fetched_at": "2026-09-12T18:00:00+00:00",
        **candidate,
    }


def test_match_reads_persona_from_vault_and_prefers_function_fit_over_popularity(tmp_path):
    vault = Vault(tmp_path / "vault")
    vault.write_persona_fact(
        title="Wants calm evening decompression",
        content="I want low-stimulation media that helps me wind down after work and clear my head.",
        reason="touchpoint-session-1",
        layer="elicited",
        source="touchpoint",
        confidence=0.9,
    )
    vault.write_hypothesis(
        title="Quiet decompression helps me reset",
        content="The user uses media to decompress when they are overstimulated after work.",
        reason="session-1",
        falsifier="If the user chooses high-energy, socially demanding media instead.",
        function="stress relief and calm decompression",
        evidence=["touchpoint-session-1"],
        confidence=0.8,
        status="active",
        occasion=Occasion(
            time_of_day="evening",
            day_type="weekday",
            session_length="short",
            label="after work",
        ),
    )

    persona_snapshot = vault.persona_snapshot()
    candidates = [
        sourced({
            "title": "A Quiet Ambient Album",
            "functions": ["calm decompression after work", "low-stimulation focus"],
            "wrong_if": "If the user wants high-energy social engagement instead of quiet decompression.",
            "popularity": 0.12,
        }),
        sourced({
            "title": "The Major Culture Hit",
            "functions": ["social buzz", "high-energy stimulation"],
            "wrong_if": "If the user is trying to relax after work and avoid stimulation.",
            "popularity": 0.99,
        }),
    ]

    results = match(persona_snapshot, candidates, k=3)
    assert results
    assert results[0].title == "A Quiet Ambient Album"
    assert results[0].function_served
    assert results[0].wrong_if
    assert all(isinstance(item, Match) for item in results)


def test_match_rejects_candidates_without_a_falsifier():
    persona_snapshot = {
        "elicited": [{"title": "Needs low-stimulation focus", "content": "I like quiet media when I need to recover."}],
        "active_hypotheses": [{"function": "calm decompression"}],
    }
    candidates = [
        sourced({
            "title": "Quiet game",
            "functions": ["calm decompression"],
            "wrong_if": "If the user needs high-energy stimulation instead of calm decompression.",
            "popularity": 0.02,
        }),
        sourced({"title": "No falsifier item", "functions": ["calm decompression"], "wrong_if": "", "popularity": 0.1}),
    ]

    results = match(persona_snapshot, candidates, k=3)
    assert len(results) == 1
    assert results[0].title == "Quiet game"


def test_match_is_cross_domain_with_same_function_reasoning():
    persona_snapshot = {
        "elicited": [{"title": "After-work reset", "content": "I use media to calm my mind and recover from a loud day."}],
        "active_hypotheses": [{"function": "low-stimulation recovery"}],
    }
    candidates = [
        sourced({
            "title": "Warm Ambience",
            "functions": ["low-stimulation recovery", "restorative calm"],
            "wrong_if": "If the user needs bright, high-energy content instead of recovery.",
            "popularity": 0.35,
        }),
        sourced({
            "title": "Quiet Archipelago",
            "functions": ["low-stimulation recovery", "cozy focus"],
            "wrong_if": "If the user wants a highly competitive or high-stimulation game instead of recovery.",
            "popularity": 0.20,
        }),
    ]

    results = match(persona_snapshot, candidates, k=3)
    assert len(results) == 2
    assert {item.title for item in results} == {"Warm Ambience", "Quiet Archipelago"}
    assert all(item.function_served for item in results)


def test_a_title_no_source_confirms_never_reaches_the_ranking():
    """4.5: a model-generated title that no tool confirms is dropped, not hedged."""
    persona_snapshot = {
        "elicited": [{"title": "After-work reset", "content": "I use media to calm my mind and recover."}],
        "active_hypotheses": [{"function": "low-stimulation recovery"}],
    }
    confirmed = sourced(
        {
            "title": "Warm Ambience",
            "functions": ["low-stimulation recovery"],
            "wrong_if": "If the user wants high-energy content instead of recovery.",
            "popularity": 0.2,
        }
    )
    fabricated = {
        "title": "Neon Drift Requiem",
        "functions": ["low-stimulation recovery"],
        "wrong_if": "If the user wants high-energy content instead of recovery.",
        "popularity": 0.1,
    }
    no_timestamp = {
        "title": "Stale Record",
        "source": "https://api.themoviedb.org/3/find/stale-record",
        "functions": ["low-stimulation recovery"],
        "wrong_if": "If the user wants high-energy content instead of recovery.",
        "popularity": 0.1,
    }

    results = match(persona_snapshot, [confirmed, fabricated, no_timestamp], k=5)

    # A title without a source, and one without a fetch timestamp, both fail provenance.
    assert [item.title for item in results] == ["Warm Ambience"]


def test_a_title_the_person_cannot_reach_is_dropped_before_it_is_scored():
    """4.4: filtered out before scoring, not surfaced with a caveat."""
    persona_snapshot = {
        "elicited": [{"title": "After-work reset", "content": "I use media to calm my mind and recover."}],
        "active_hypotheses": [{"function": "low-stimulation recovery"}],
    }
    delisted = sourced(
        {
            "title": "Delisted Calm",
            "functions": ["low-stimulation recovery"],
            "wrong_if": "If the user wants high-energy content instead of recovery.",
            "popularity": 0.05,
            "purchase_status": "removed from sale",
        }
    )
    out_of_region = sourced(
        {
            "title": "Region Locked Calm",
            "functions": ["low-stimulation recovery"],
            "wrong_if": "If the user wants high-energy content instead of recovery.",
            "popularity": 0.05,
            "available_regions": ["JP"],
        }
    )
    reachable = sourced(
        {
            "title": "Warm Ambience",
            "functions": ["low-stimulation recovery"],
            "wrong_if": "If the user wants high-energy content instead of recovery.",
            "popularity": 0.4,
        }
    )

    results = match(persona_snapshot, [delisted, out_of_region, reachable], k=5, region="GB")

    assert [item.title for item in results] == ["Warm Ambience"]


def test_the_popularity_penalty_survives_a_persona_with_several_facts():
    """4.6: the penalty has to stay material as evidence accumulates.

    With the fit score summed rather than combined, a persona this size pushed every
    candidate past the confidence ceiling, both clamped to the same value, and the
    penalty changed nothing. This is the regression test for that.
    """
    persona_snapshot = {
        "elicited": [
            {"title": "After-work reset", "content": "I use media to calm my mind and recover from a loud day."},
            {"title": "Quiet evenings", "content": "I want low-stimulation calm media to wind down."},
            {"title": "Decompression", "content": "Restful, soothing, gentle things help me decompress."},
            {"title": "Recovery", "content": "I need recovery and rest rather than challenge."},
        ],
        "active_hypotheses": [
            {"function": "low-stimulation recovery"},
            {"function": "calm decompression"},
        ],
    }
    shared = {
        "functions": ["low-stimulation calm recovery and decompression"],
        "wrong_if": "If the user wants a challenge loop instead of recovery.",
    }
    long_tail = sourced({"title": "Quiet Archipelago", "popularity": 0.05, **shared})
    blockbuster = sourced({"title": "The Major Culture Hit", "popularity": 0.95, **shared})

    results = match(persona_snapshot, [long_tail, blockbuster], k=5)

    assert [item.title for item in results] == ["Quiet Archipelago", "The Major Culture Hit"]
    # Equal function fit, so the whole difference is the attention penalty.
    gap = results[0].confidence - results[1].confidence
    assert gap > 0.2, f"popularity penalty is being swamped: gap was {gap}"
    assert all(0.0 <= item.confidence <= 0.99 for item in results)


def test_fit_score_is_bounded_however_much_evidence_agrees():
    persona_snapshot = {
        "elicited": [
            {"title": f"Calm {index}", "content": "low-stimulation calm recovery decompression rest"}
            for index in range(20)
        ],
        "active_hypotheses": [],
    }
    candidate = sourced(
        {
            "title": "Warm Ambience",
            "functions": ["low-stimulation calm recovery decompression rest"],
            "wrong_if": "If the user wants high-energy content instead of recovery.",
            "popularity": 0.0,
        }
    )

    results = match(persona_snapshot, [candidate], k=5)

    assert results[0].confidence <= 0.99
