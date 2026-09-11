import pytest

from mind.ai import Opus5Gateway
from mind.obsidian_brain import ObsidianBrain


def test_obsidian_brain_ingests_and_summarizes(tmp_path):
    brain = ObsidianBrain(vault_root=tmp_path / "vault")
    result = brain.ingest("The Matrix", "A hero is trapped in a simulation and learns the truth.")

    assert result["title"] == "The Matrix"
    # Notes are written through Vault into a typed folder, so they carry frontmatter,
    # a layer and a git commit with a reason instead of landing loose at the root.
    assert (tmp_path / "vault" / "works" / "The Matrix.md").exists()
    assert isinstance(brain.summarize("The Matrix", "A hero is trapped in a simulation and learns the truth."), str)


def test_ingest_records_provenance_and_rejects_an_unknown_kind(tmp_path):
    brain = ObsidianBrain(vault_root=tmp_path / "vault")
    brain.ingest(
        "Solaris",
        "A slow film about grief.",
        kind="works",
        layer="elicited",
        source="user",
        reason="user described this",
    )

    content = (tmp_path / "vault" / "works" / "Solaris.md").read_text(encoding="utf-8")
    assert "layer: elicited" in content
    assert "source: user" in content

    with pytest.raises(ValueError, match="kind must be one of"):
        brain.ingest("Nope", "body", kind="../../escape")


def test_brain_maps_links_and_backlinks(tmp_path):
    brain = ObsidianBrain(vault_root=tmp_path / "vault")
    brain.ingest("The Matrix", "A hero escapes a simulation.")
    brain.ingest("Simulation stories", "Compare [[The Matrix]] with other simulation films.")

    backlinks = brain.backlinks("The Matrix")
    assert [note["title"] for note in backlinks] == ["Simulation stories"]

    links = brain.links("Simulation stories")
    assert [note["title"] for note in links] == ["The Matrix"]


def test_layer_is_never_invented_for_a_note_without_one(tmp_path):
    brain = ObsidianBrain(vault_root=tmp_path / "vault")
    brain.ingest("Unlabelled", "No layer was declared for this note.")

    note = next(item for item in brain.notes() if item.title == "Unlabelled")
    assert note.layer is None


def test_related_returns_link_neighbours_with_a_stub_embedder(tmp_path):
    from mind.embeddings import StubEmbedder

    brain = ObsidianBrain(vault_root=tmp_path / "vault", embedder=StubEmbedder())
    brain.ingest("The Matrix", "A hero escapes a simulation.")
    brain.ingest("Simulation stories", "Compare [[The Matrix]] with other simulation films.")

    related = brain.related("simulation", limit=5, seed_title="The Matrix")
    titles = {note["title"] for note in related}
    assert "The Matrix" in titles
    assert "Simulation stories" in titles
    assert all("layer" in note and "relation" in note for note in related)


def test_route_treats_a_question_about_a_claim_as_a_question(tmp_path):
    brain = ObsidianBrain(vault_root=tmp_path / "vault")

    assert brain.route("What is the central claim of this work?") == "question"
    assert brain.route("I think the ending is unearned.") == "claim"
    assert brain.route("") == "question"


def test_opus_gateway_falls_back_cleanly_without_api_key(monkeypatch):
    # The gateway reads the key from the environment when none is passed, so the
    # "without a key" case has to remove it rather than rely on it being absent.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    gateway = Opus5Gateway(api_key=None)
    plan = gateway.plan("Summarize this claim")

    assert plan["model"] == "claude-opus-5"
    assert plan["offline"] is True
    assert "evidence-first" in plan["strategy"]


def test_stub_embedder_is_testable_without_model_download():
    from mind.embeddings import StubEmbedder

    vector = StubEmbedder().embed("A hopeful story about escape.")
    assert len(vector) == 8
    assert all(isinstance(v, float) for v in vector)
