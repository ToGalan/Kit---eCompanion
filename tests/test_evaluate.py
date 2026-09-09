import json

from evaluate import evaluate_matching


def _dataset():
    distractors = [
        {"title": f"Title {index}", "functions": ["challenge", "skill mastery"], "wrong_if": "If the user wants quiet decompression and low stimulation.", "popularity": 0.74}
        for index in range(1, 61)
    ]
    common_catalog = [
        {"title": "Quiet Ambient Album", "functions": ["calm decompression", "low-stimulation recovery"], "wrong_if": "If the user wants high-energy stimulation instead of calm decompression.", "popularity": 0.05},
        {"title": "City Pulse", "functions": ["high-energy stimulation", "social buzz"], "wrong_if": "If the user wants quiet recovery and low stimulation.", "popularity": 0.90},
        {"title": "Cozy Adventure", "functions": ["cozy focus", "stress relief"], "wrong_if": "If the user needs intense challenge and high arousal instead of stress relief.", "popularity": 0.40},
        {"title": "Rift Runner", "functions": ["challenge", "competition", "skill mastery"], "wrong_if": "If the user wants to unwind rather than push their skill ceiling.", "popularity": 0.70},
        {"title": "Moonlit Story", "functions": ["story", "worldbuilding", "narrative arc"], "wrong_if": "If the user wants recovery and not a narrative-heavy commitment.", "popularity": 0.55},
        {"title": "Neon Drift", "functions": ["energy", "adrenaline", "social buzz"], "wrong_if": "If the user wants calm decompression and low arousal.", "popularity": 0.82},
        {"title": "Hushed Tides", "functions": ["calm decompression", "focus", "quiet immersion"], "wrong_if": "If the user wants loud social stimulation.", "popularity": 0.18},
        {"title": "Night Garden", "functions": ["comfort", "cozy focus", "stress relief"], "wrong_if": "If the user wants intense challenge or social noise.", "popularity": 0.33},
        {"title": "Spark Circuit", "functions": ["challenge", "difficulty", "mastery"], "wrong_if": "If the user wants gentle recovery instead of hard mastery loops.", "popularity": 0.64},
        {"title": "Open Sky", "functions": ["discovery", "exploration", "curiosity"], "wrong_if": "If the user wants to avoid a wandering exploratory loop.", "popularity": 0.48},
        {"title": "Signal Bloom", "functions": ["social", "conversation", "community"], "wrong_if": "If the user wants to avoid social energy and keep things calm.", "popularity": 0.76},
        {"title": "Falling Echoes", "functions": ["comfort", "nostalgia", "quiet focus"], "wrong_if": "If the user wants a loud, high-energy or competitive experience.", "popularity": 0.29},
        *distractors,
    ]

    return [
        {
            "user_id": "u1",
            "signals": [
                {"title": "after work reset", "content": "I need quiet, low-stimulation media to unwind after work."},
                {"title": "focus mode", "content": "I like media that helps me decompress and stay calm."},
            ],
            "engaged": [{"title": "Quiet Ambient Album"}],
            "catalog": common_catalog,
        },
        {
            "user_id": "u2",
            "signals": [
                {"title": "recharge", "content": "I use media to recover from a loud day and stay in a calm headspace."},
            ],
            "engaged": [{"title": "Quiet Ambient Album"}],
            "catalog": common_catalog,
        },
    ]


def test_evaluate_matching_reports_hit_at_10_for_persona_vs_baselines():
    report = evaluate_matching(_dataset(), seeds=(0, 1, 2), k=10)

    assert report["metric"] == "hit@10"
    assert set(report) >= {"persona", "random", "popularity", "seeds_used", "verdict", "json_report", "human_review_sheet"}
    assert report["persona"]["ci"]
    assert report["random"]["ci"]
    assert report["popularity"]["ci"]
    assert len(report["seeds_used"]) == 3
    assert report["persona"]["variance"] >= 0.0
    assert report["random"]["variance"] >= 0.0
    assert report["popularity"]["variance"] >= 0.0
    assert report["persona"]["mean"] > report["random"]["mean"]
    assert report["persona"]["mean"] > report["popularity"]["mean"]
    assert report["verdict"].startswith("PASS")

    payload = json.loads(report["json_report"])
    assert payload["metric"] == "hit@10"
    assert "persona" in payload
    assert "random" in payload
    assert "popularity" in payload

    sheet = report["human_review_sheet"]
    assert "Human grader review sheet" in sheet
    assert "hit@10" in sheet.lower()
    assert "PASS" in sheet


def test_evaluate_matching_fails_plainly_when_persona_loses_to_baselines():
    bad_dataset = [
        {
            "user_id": "u1",
            "signals": [
                {"title": "I like loud noise", "content": "I want chaotic stimulation and social buzz."},
            ],
            "engaged": [{"title": "High Energy Rush"}],
            "catalog": [
                {
                    "title": "High Energy Rush",
                    "functions": ["social buzz", "high-energy stimulation"],
                    "wrong_if": "If the user wants quiet recovery and low stimulation.",
                    "popularity": 0.99,
                },
                {
                    "title": "Quiet Ambient Album",
                    "functions": ["calm decompression", "low-stimulation recovery"],
                    "wrong_if": "If the user wants high-energy stimulation instead of calm decompression.",
                    "popularity": 0.05,
                },
            ],
        },
    ]

    report = evaluate_matching(bad_dataset, seeds=(0,), k=10)
    assert report["persona"]["mean"] <= report["random"]["mean"]
    assert report["persona"]["mean"] <= report["popularity"]["mean"]
    assert report["verdict"].startswith("FAIL")
    assert "did not beat" in report["verdict"]
