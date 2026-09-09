from pathlib import Path

from kit.vault import Vault
from mind.matching import Match, match


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
    )

    persona_snapshot = vault.persona_snapshot()
    candidates = [
        {
            "title": "A Quiet Ambient Album",
            "functions": ["calm decompression after work", "low-stimulation focus"],
            "wrong_if": "If the user wants high-energy social engagement instead of quiet decompression.",
            "popularity": 0.12,
        },
        {
            "title": "The Major Culture Hit",
            "functions": ["social buzz", "high-energy stimulation"],
            "wrong_if": "If the user is trying to relax after work and avoid stimulation.",
            "popularity": 0.99,
        },
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
        {
            "title": "Quiet game",
            "functions": ["calm decompression"],
            "wrong_if": "If the user needs high-energy stimulation instead of calm decompression.",
            "popularity": 0.02,
        },
        {"title": "No falsifier item", "functions": ["calm decompression"], "wrong_if": "", "popularity": 0.1},
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
        {
            "title": "Warm Ambience",
            "functions": ["low-stimulation recovery", "restorative calm"],
            "wrong_if": "If the user needs bright, high-energy content instead of recovery.",
            "popularity": 0.35,
        },
        {
            "title": "Quiet Archipelago",
            "functions": ["low-stimulation recovery", "cozy focus"],
            "wrong_if": "If the user wants a highly competitive or high-stimulation game instead of recovery.",
            "popularity": 0.20,
        },
    ]

    results = match(persona_snapshot, candidates, k=3)
    assert len(results) == 2
    assert {item.title for item in results} == {"Warm Ambience", "Quiet Archipelago"}
    assert all(item.function_served for item in results)
