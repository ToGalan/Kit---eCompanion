import zipfile

import pytest

from kit.vault import Occasion, Vault, write_claim


@pytest.fixture
def temp_vault(tmp_path):
    return Vault(tmp_path / "vault")


def test_claim_requires_falsifier(temp_vault):
    with pytest.raises(ValueError, match="falsifier"):
        write_claim(
            temp_vault,
            title="Strong claim",
            content="This is a claim.",
            reason="initial analysis",
            falsifier=None,
        )


def test_notes_section_survives_rewrite(temp_vault):
    note_path = temp_vault.write_work(
        title="The Matrix",
        content="# The Matrix\n\n## Notes\nThis is a manual note.\n",
        reason="initial draft",
    )

    temp_vault.rewrite_note(
        title="The Matrix",
        content="# The Matrix\n\n## Notes\nThis is still here.\n\n## Summary\nNew summary.\n",
        kind="works",
        reason="updated analysis",
    )

    rewritten = note_path.read_text(encoding="utf-8")
    assert "## Notes\nThis is still here." in rewritten
    assert "## Summary\nNew summary." in rewritten


def test_drop_source_removes_evidence_and_orphans_claims(temp_vault):
    source_path = temp_vault.write_source(title="Source One", content="Source about the work.", reason="capture source")
    temp_vault.write_claim(
        title="Claim A",
        content="[[The Matrix]] is a good film.",
        reason="record claim",
        falsifier="Critic Bob",
        dimension="quality",
        confidence=0.7,
        work="The Matrix",
        evidence=[source_path.stem],
    )
    temp_vault.write_claim(
        title="Claim B",
        content="[[The Matrix]] is a bad film.",
        reason="record contrary claim",
        falsifier="Critic Eve",
        dimension="quality",
        confidence=0.6,
        work="The Matrix",
        evidence=[source_path.stem],
    )

    temp_vault.drop_source(source_path.stem)

    assert not temp_vault.get_note("Claim A")
    assert not temp_vault.get_note("Claim B")
    assert not (temp_vault.path / "sources" / f"{source_path.stem}.md").exists()


def test_backlinks_returns_claims_about_work(temp_vault):
    temp_vault.write_work(title="The Matrix", content="Work note for The Matrix.", reason="capture work")
    temp_vault.write_claim(
        title="Claim A",
        content="[[The Matrix]] has strong worldbuilding.",
        reason="record claim",
        falsifier="Critic Bob",
        dimension="quality",
        confidence=0.8,
        work="The Matrix",
        evidence=[],
    )
    temp_vault.write_claim(
        title="Claim B",
        content="[[The Matrix]] is underappreciated.",
        reason="record second claim",
        falsifier="Critic Eve",
        dimension="quality",
        confidence=0.7,
        work="The Matrix",
        evidence=[],
    )
    temp_vault.write_claim(
        title="Claim C",
        content="[[Blade Runner]] is a classic.",
        reason="record unrelated claim",
        falsifier="Critic Dan",
        dimension="quality",
        confidence=0.9,
        work="Blade Runner",
        evidence=[],
    )

    backlinks = temp_vault.backlinks("The Matrix")

    assert {claim["title"] for claim in backlinks} == {"Claim A", "Claim B"}


def test_obsidian_note_layout_is_valid(temp_vault):
    temp_vault.write_work(title="The Matrix", content="Work note for The Matrix.", reason="capture work")
    temp_vault.write_claim(
        title="Claim A",
        content="[[The Matrix]] is influential.",
        reason="record claim",
        falsifier="Critic Bob",
        dimension="quality",
        confidence=0.8,
        work="The Matrix",
        evidence=[],
    )

    assert (temp_vault.path / "works").is_dir()
    assert (temp_vault.path / "claims").is_dir()
    assert (temp_vault.path / "questions").is_dir()
    assert (temp_vault.path / "sources").is_dir()
    assert (temp_vault.path / "persona").is_dir()
    assert (temp_vault.path / "hypotheses").is_dir()
    assert (temp_vault.path / "works" / "The Matrix.md").exists()
    assert "title: The Matrix" in (temp_vault.path / "works" / "The Matrix.md").read_text(
        encoding="utf-8"
    )


def test_persona_fact_and_hypothesis_are_written_with_timestamps(temp_vault):
    occasion = Occasion(
        time_of_day="evening",
        day_type="weekday",
        session_length="short",
        label="winding down",
    )

    fact_path = temp_vault.write_persona_fact(
        title="Prefers quiet evenings",
        content="The user often says they want a relaxing, low-stimulation way to end the day.",
        reason="capture elicited fact",
        layer="elicited",
        source="user interview",
        confidence=0.9,
    )
    hypothesis_path = temp_vault.write_hypothesis(
        title="Evening media regulates mood",
        content="Low-stimulation media helps the user reduce stress after a noisy workday.",
        reason="record active hypothesis",
        falsifier="Counterexample: the user still prefers social media after work.",
        function="regulates mood",
        evidence=["user interview"],
        confidence=0.8,
        status="active",
        occasion=occasion,
    )

    fact_text = fact_path.read_text(encoding="utf-8")
    hypothesis_text = hypothesis_path.read_text(encoding="utf-8")

    assert "updated:" in fact_text
    assert "updated:" in hypothesis_text
    assert "layer: elicited" in fact_text
    assert "function: regulates mood" in hypothesis_text
    assert "occasion_time_of_day: evening" in hypothesis_text
    assert "occasion_day_type: weekday" in hypothesis_text
    assert "occasion_session_length: short" in hypothesis_text

    snapshot = temp_vault.persona_snapshot()
    assert snapshot["elicited"][0]["title"] == "Prefers quiet evenings"
    assert "active_hypotheses" not in snapshot

    occasion_snapshot = temp_vault.persona_snapshot(occasion)
    assert occasion_snapshot["active_hypotheses"][0]["function"] == "regulates mood"


def test_persona_fact_rejects_invalid_layer(temp_vault):
    with pytest.raises(ValueError, match="layer"):
        temp_vault.write_persona_fact(
            title="Nope",
            content="invalid layer",
            reason="bad layer",
            layer="unknown",
            source="user",
            confidence=0.1,
        )


def test_hypothesis_requires_falsifier_function_and_occasion(temp_vault):
    with pytest.raises(ValueError, match="falsifier"):
        temp_vault.write_hypothesis(
            title="Mood support",
            content="This helps them relax.",
            reason="record hypothesis",
            falsifier=None,
            function="regulates mood",
            evidence=[],
            confidence=0.5,
            status="active",
            occasion=Occasion(time_of_day="evening", day_type="weekday", session_length="short"),
        )

    with pytest.raises(ValueError, match="function"):
        temp_vault.write_hypothesis(
            title="Mood support",
            content="This helps them relax.",
            reason="record hypothesis",
            falsifier="Someone could test this.",
            function="",
            evidence=[],
            confidence=0.5,
            status="active",
            occasion=Occasion(time_of_day="evening", day_type="weekday", session_length="short"),
        )

    with pytest.raises(ValueError, match="occasion"):
        temp_vault.write_hypothesis(
            title="Mood support",
            content="This helps them relax.",
            reason="record hypothesis",
            falsifier="Someone could test this.",
            function="regulates mood",
            evidence=[],
            confidence=0.5,
            status="active",
            occasion=None,
        )


def test_hypotheses_are_ranked_by_occasion_and_keep_untested_explicit(temp_vault):
    evening = Occasion(time_of_day="evening", day_type="weekday", session_length="short", label="winding down")
    night = Occasion(time_of_day="night", day_type="weekday", session_length="medium", label="background while working")

    temp_vault.write_hypothesis(
        title="Evening regulation",
        content="This helps the user wind down.",
        reason="record regulation fit",
        falsifier="A counterexample would still be plausible.",
        function="regulation",
        evidence=["user interview"],
        confidence=0.82,
        status="active",
        occasion=evening,
    )
    temp_vault.write_hypothesis(
        title="Night mastery",
        content="The user still wants challenge after work.",
        reason="record night mastery hypothesis",
        falsifier="A quiet low-focus evening could also be true.",
        function="mastery",
        evidence=["usage signal"],
        confidence=0.66,
        status="active",
        occasion=night,
    )
    temp_vault.write_hypothesis(
        title="Night regulation",
        content="The user may also seek calm while working late.",
        reason="record untested night regulation hypothesis",
        falsifier="This is not yet observed.",
        function="regulation",
        evidence=[],
        confidence=None,
        status="active",
        occasion=night,
    )

    evening_set = temp_vault.hypotheses_for(evening)
    assert evening_set["ranked"][0]["function"] == "regulation"
    assert evening_set["untested"] == []

    night_set = temp_vault.hypotheses_for(night)
    assert [item["function"] for item in night_set["ranked"]] == ["mastery"]
    assert night_set["untested"][0]["function"] == "regulation"
    assert night_set["untested"][0]["confidence"] is None


def test_history_revision_and_export(temp_vault):
    note_path = temp_vault.write_work(
        title="The Matrix",
        content="Initial version.",
        reason="initial draft",
    )
    temp_vault.write_work(
        title="The Matrix",
        content="Revised version.",
        reason="revision update",
    )

    entries = temp_vault.history("The Matrix", "works")
    assert len(entries) == 2
    assert entries[0]["reason"] == "revision update"
    assert entries[1]["reason"] == "initial draft"

    current = note_path.read_text(encoding="utf-8")
    assert "Revised version." in current

    first_commit = entries[1]["commit"]
    original = temp_vault.revision("The Matrix", "works", first_commit)
    assert "Initial version." in original

    zip_path = temp_vault.export()
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path, "r") as archive:
        assert "manifest.json" in archive.namelist()
        assert "works/The Matrix.md" in archive.namelist()
