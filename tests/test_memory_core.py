"""Tests for the memory core (event-log + scope enforcement).

Pins the security invariants carried through steps 2-3 — namespace isolation at
the storage boundary — and the event-sourcing contract (ingest appends a raw
event; the context store is a pure projection that rebuilds from the log).
"""

from tenet import HashingEmbedder, MemoryCore
from tenet.events import InMemoryEventLog
from tenet.events.taxonomy import MEMORY_RAW_APPENDED
from tenet.scope import ScopeGrant


def _core() -> MemoryCore:
    return MemoryCore(embedder=HashingEmbedder())


def _grant(*namespaces: str) -> ScopeGrant:
    return ScopeGrant.issue(principal="tester", task_id="t", namespaces=set(namespaces))


def test_retrieval_ranks_relevant_record_first():
    core = _core()
    core.ingest("deploys to us-east-1 on Fridays", namespace="a", source="x")
    core.ingest("unrelated note about lunch menus", namespace="a", source="y")

    results = core.retrieve("where does it deploy", _grant("a"), namespace="a")
    assert results
    assert "deploy" in results[0].record.text


def test_namespace_filter_holds_even_when_grant_permits_both():
    # A grant for both namespaces, but a query targeting one, must not surface
    # the other: the storage-boundary namespace filter (stage 2) still applies.
    core = _core()
    core.ingest("backups stored in Glacier", namespace="beta", source="b")
    both = _grant("alpha", "beta")

    assert core.retrieve("backups in Glacier", both, namespace="alpha") == []
    hit = core.retrieve("backups in Glacier", both, namespace="beta")
    assert any("Glacier" in r.record.text for r in hit)


def test_ingest_appends_one_raw_event_with_provenance():
    core = _core()
    raw = core.ingest("hello world", namespace="a", source="src.md")

    events = list(core.log)
    assert len(events) == 1                       # deterministic ctx emits none
    ev = events[0]
    assert ev.event_type == MEMORY_RAW_APPENDED
    assert ev.event_id == raw.raw_id
    assert ev.payload["content"] == "hello world"
    assert core.log.verify() is True

    ctx = core.context(f"{raw.raw_id}:0")
    assert ctx is not None
    assert ctx.raw_id == raw.raw_id
    assert ctx.raw_content_hash == raw.content_hash
    assert ctx.embedding


def test_context_store_is_a_projection_rebuildable_from_the_log():
    core = _core()
    core.ingest("deploys to us-east-1", namespace="a", source="x")
    core.ingest("backups in Glacier", namespace="a", source="y")
    g = _grant("a")

    before = [(r.record.context_id, round(r.score, 6))
              for r in core.retrieve("where does it deploy", g, namespace="a")]
    core.rebuild_projections()
    after = [(r.record.context_id, round(r.score, 6))
             for r in core.retrieve("where does it deploy", g, namespace="a")]
    assert before == after and before


def test_core_can_be_built_over_a_shared_prepopulated_log():
    log = InMemoryEventLog()
    c1 = MemoryCore(embedder=HashingEmbedder(), log=log)
    c1.ingest("deploys to us-east-1", namespace="a", source="x")

    c2 = MemoryCore(embedder=HashingEmbedder(), log=log)
    c2.rebuild_projections()

    g = _grant("a")
    r1 = c1.retrieve("deploy", g, namespace="a")
    r2 = c2.retrieve("deploy", g, namespace="a")
    assert [x.record.context_id for x in r1] == [x.record.context_id for x in r2]
    assert r2
