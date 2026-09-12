
from app.touchpoint import Touchpoint, TouchpointStore


def test_touchpoint_opens_with_a_short_targeted_question_and_goal(tmp_path):
    store = TouchpointStore(db_path=tmp_path / "touchpoints.db")
    touchpoint = Touchpoint(
        user="alice",
        session_id="session-1",
        goal="What music has been in rotation lately?",
        days_since_contact=11,
        persona_snapshot={
            "elicited": [
                {"title": "Prefers quiet evenings", "content": "I like low stimulation and a calm atmosphere."},
                {"title": "No strong music preference yet", "content": "I have not said much about music."},
            ]
        },
        recent_signals=[
            {"domain": "youtube.com", "signal": "save"},
            {"domain": "spotify.com", "signal": "circle"},
        ],
        store=store,
    )

    assert "It’s been 11 days" in touchpoint.opening
    assert "What music" in touchpoint.opening
    assert "guilting" not in touchpoint.opening.lower()


def test_touchpoint_rate_limits_one_start_per_user_per_day(tmp_path):
    store = TouchpointStore(db_path=tmp_path / "touchpoints.db")
    first = Touchpoint.create_for_user(
        "alice",
        persona_snapshot={},
        recent_signals=[],
        days_since_contact=2,
        store=store,
    )
    second = Touchpoint.create_for_user(
        "alice",
        persona_snapshot={},
        recent_signals=[],
        days_since_contact=0,
        store=store,
    )

    assert first is not None
    assert second is None


def test_durable_fact_from_touchpoint_is_written_to_persona_vault(tmp_path):
    store = TouchpointStore(db_path=tmp_path / "touchpoints.db")
    vault_root = tmp_path / "vault"
    touchpoint = Touchpoint(
        user="alice",
        session_id="session-2",
        goal="What music has been in rotation lately?",
        days_since_contact=3,
        persona_snapshot={"elicited": []},
        recent_signals=[],
        vault=None,
        store=store,
    )
    touchpoint.vault = __import__("kit.vault", fromlist=["Vault"]).Vault(vault_root)

    result = touchpoint.capture_durable_fact(
        "I mostly listen to jazz and ambient music at night, and I keep returning to calm instrumental sets.",
        session_id="session-2",
    )

    assert result is not None
    fact_text = result.read_text(encoding="utf-8")
    assert "layer: elicited" in fact_text
    assert touchpoint.vault.history(result.stem, "persona")[0]["reason"] == "session-2"
