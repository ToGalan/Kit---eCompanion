import pytest

from mind.brain import check_line, run_turn, validate_claim


def test_validate_claim_refuses_without_falsifier():
    with pytest.raises(ValueError, match="falsifier"):
        validate_claim({"text": "This is a claim.", "falsifier": ""})


def test_check_line_blocks_banned_phrasings():
    assert check_line("This is guaranteed to be true.") is True
    assert check_line("The review is obviously biased.") is True
    assert check_line("A careful reading suggests a likely pattern.") is False


def test_run_turn_returns_offline_useful_result_when_catalogs_fail():
    result = run_turn(
        "What is the production status of the work?",
        claims=[],
        catalogs={
            "steam": lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
            "tmdb": lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
            "anilist": lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
            "musicbrainz": lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
        },
    )

    assert result["offline"] is True
    assert "offline" in result["answer"].lower()
    assert len(result["answer"]) > 20
