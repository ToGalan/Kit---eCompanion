import pytest

from kit.vault import Vault, write_claim


@pytest.fixture
def temp_vault(tmp_path):
    return Vault(tmp_path / "vault")


def test_claim_requires_falsifier(temp_vault):
    with pytest.raises(ValueError, match="falsifier"):
        write_claim(
            temp_vault,
            title="Strong claim",
            content="This is a claim.",
            falsifier=None,
        )


def test_notes_section_survives_rewrite(temp_vault):
    note_path = temp_vault.write_work(
        title="The Matrix",
        content="# The Matrix\n\n## Notes\nThis is a manual note.\n",
    )

    temp_vault.rewrite_note(
        title="The Matrix",
        content="# The Matrix\n\n## Notes\nThis is still here.\n\n## Summary\nNew summary.\n",
        kind="works",
    )

    rewritten = note_path.read_text(encoding="utf-8")
    assert "## Notes\nThis is still here." in rewritten
    assert "## Summary\nNew summary." in rewritten


def test_drop_source_removes_evidence_and_orphans_claims(temp_vault):
    source_path = temp_vault.write_source(title="Source One", content="Source about the work.")
    temp_vault.write_claim(
        title="Claim A",
        content="[[The Matrix]] is a good film.",
        falsifier="Critic Bob",
        dimension="quality",
        confidence=0.7,
        work="The Matrix",
        evidence=[source_path.stem],
    )
    temp_vault.write_claim(
        title="Claim B",
        content="[[The Matrix]] is a bad film.",
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
    temp_vault.write_work(title="The Matrix", content="Work note for The Matrix.")
    temp_vault.write_claim(
        title="Claim A",
        content="[[The Matrix]] has strong worldbuilding.",
        falsifier="Critic Bob",
        dimension="quality",
        confidence=0.8,
        work="The Matrix",
        evidence=[],
    )
    temp_vault.write_claim(
        title="Claim B",
        content="[[The Matrix]] is underappreciated.",
        falsifier="Critic Eve",
        dimension="quality",
        confidence=0.7,
        work="The Matrix",
        evidence=[],
    )
    temp_vault.write_claim(
        title="Claim C",
        content="[[Blade Runner]] is a classic.",
        falsifier="Critic Dan",
        dimension="quality",
        confidence=0.9,
        work="Blade Runner",
        evidence=[],
    )

    backlinks = temp_vault.backlinks("The Matrix")

    assert {claim["title"] for claim in backlinks} == {"Claim A", "Claim B"}


def test_obsidian_note_layout_is_valid(temp_vault):
    temp_vault.write_work(title="The Matrix", content="Work note for The Matrix.")
    temp_vault.write_claim(
        title="Claim A",
        content="[[The Matrix]] is influential.",
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
    assert (temp_vault.path / "works" / "The Matrix.md").exists()
    assert "title: The Matrix" in (temp_vault.path / "works" / "The Matrix.md").read_text(
        encoding="utf-8"
    )
