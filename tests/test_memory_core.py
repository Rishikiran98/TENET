"""Tests for the memory core after the event-log refactor (build step 2).

Two things are pinned here:
  - the security invariant carried over from slice 1, now namespace isolation;
  - the event-sourcing contract: ingest appends a raw event, and the context
    store is a pure projection that rebuilds identically from the log.
"""

from tenet import HashingEmbedder, MemoryCore
from tenet.events import InMemoryEventLog
from tenet.events.taxonomy import MEMORY_RAW_APPENDED


def _core() -> MemoryCore:
    return MemoryCore(embedder=HashingEmbedder())


def test_retrieval_ranks_relevant_record_first():
    core = _core()
    core.ingest("deploys to us-east-1 on Fridays", namespace="a", source="x")
    core.ingest("unrelated note about lunch menus", namespace="a", source="y")

    results = core.retrieve("where does it deploy", namespace="a")
    assert results
    assert "deploy" in results[0].record.text


def test_namespace_isolation_blocks_cross_namespace_retrieval():
    core = _core()
    core.ingest("backups stored in Glacier", namespace="beta", source="b")

    # Best match overall, but out of namespace for alpha -> must not surface.
    assert core.retrieve("backups in Glacier", namespace="alpha") == []

    # Same query, correct namespace -> surfaces.
    results = core.retrieve("backups in Glacier", namespace="beta")
    assert any("Glacier" in r.record.text for r in results)


def test_ingest_appends_one_raw_event_with_provenance():
    core = _core()
    raw = core.ingest("hello world", namespace="a", source="src.md")

    # Exactly one event per ingest (deterministic contextualizer emits none).
    events = list(core.log)
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == MEMORY_RAW_APPENDED
    assert ev.namespace == "a"
    assert ev.event_id == raw.raw_id                 # raw_id IS the event id
    assert ev.payload["content"] == "hello world"
    assert ev.payload["source"] == "src.md"
    assert core.log.verify() is True

    # The derived context carries provenance back to the raw record.
    ctx = core.context(f"{raw.raw_id}:0")
    assert ctx is not None
    assert ctx.raw_id == raw.raw_id
    assert ctx.raw_content_hash == raw.content_hash
    assert ctx.embedding


def test_context_store_is_a_projection_rebuildable_from_the_log():
    core = _core()
    core.ingest("deploys to us-east-1", namespace="a", source="x")
    core.ingest("backups in Glacier", namespace="a", source="y")

    before = [(r.record.context_id, round(r.score, 6))
              for r in core.retrieve("where does it deploy", namespace="a")]

    # Drop and re-fold both projections straight from the log.
    core.rebuild_projections()

    after = [(r.record.context_id, round(r.score, 6))
             for r in core.retrieve("where does it deploy", namespace="a")]
    assert before == after and before  # identical, and non-empty


def test_core_can_be_built_over_a_shared_prepopulated_log():
    # The log is the source of truth: a fresh core over the same log reproduces
    # the same memory by replay, without re-ingesting.
    log = InMemoryEventLog()
    c1 = MemoryCore(embedder=HashingEmbedder(), log=log)
    c1.ingest("deploys to us-east-1", namespace="a", source="x")

    c2 = MemoryCore(embedder=HashingEmbedder(), log=log)
    c2.rebuild_projections()

    r1 = c1.retrieve("deploy", namespace="a")
    r2 = c2.retrieve("deploy", namespace="a")
    assert [x.record.context_id for x in r1] == [x.record.context_id for x in r2]
    assert r2
