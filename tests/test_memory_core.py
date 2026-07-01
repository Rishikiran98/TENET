"""Tests for the memory core.

The scope-isolation test is the important one: it pins a *security invariant*
as an executable assertion. When the governance gate and agent are added later,
this guarantee is what they rest on.
"""

from tenet import HashingEmbedder, InMemoryStore, MemoryCore


def _core() -> MemoryCore:
    return MemoryCore(embedder=HashingEmbedder(), store=InMemoryStore())


def test_retrieval_ranks_relevant_record_first():
    core = _core()
    core.ingest("deploys to us-east-1 on Fridays", scope="a", source="x")
    core.ingest("unrelated note about lunch menus", scope="a", source="y")

    results = core.retrieve("where does it deploy", scope="a")
    assert results
    assert "deploy" in results[0].record.content


def test_scope_isolation_blocks_cross_scope_retrieval():
    core = _core()
    core.ingest("backups stored in Glacier", scope="beta", source="b")

    # Best match overall, but out of scope for alpha -> must not surface.
    results = core.retrieve("backups in Glacier", scope="alpha")
    assert results == []

    # Same query, correct scope -> surfaces.
    results = core.retrieve("backups in Glacier", scope="beta")
    assert any("Glacier" in r.record.content for r in results)


def test_ingest_attaches_provenance_and_embedding():
    core = _core()
    rec = core.ingest("hello world", scope="a", source="src.md")
    assert rec.source == "src.md"
    assert rec.embedding is not None
    assert rec.created_at is not None
