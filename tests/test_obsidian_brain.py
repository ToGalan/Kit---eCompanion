from mind.ai import Opus5Gateway
from mind.obsidian_brain import ObsidianBrain


def test_obsidian_brain_ingests_and_summarizes(tmp_path):
    brain = ObsidianBrain(vault_root=tmp_path / "vault")
    result = brain.ingest("The Matrix", "A hero is trapped in a simulation and learns the truth.")

    assert result["title"] == "The Matrix"
    assert (tmp_path / "vault" / "The Matrix.md").exists()
    assert isinstance(brain.summarize("The Matrix", "A hero is trapped in a simulation and learns the truth."), str)


def test_opus_gateway_falls_back_cleanly_without_api_key():
    gateway = Opus5Gateway(api_key=None)
    plan = gateway.plan("Summarize this claim")

    assert plan["model"] == "claude-opus-5"
    assert plan["offline"] is True
    assert "evidence-first" in plan["strategy"]


def test_deterministic_embedder_is_testable_without_torch():
    from mind.embeddings import DeterministicEmbedder

    vector = DeterministicEmbedder().embed("A hopeful story about escape.")
    assert len(vector) == 8
    assert all(isinstance(v, float) for v in vector)
