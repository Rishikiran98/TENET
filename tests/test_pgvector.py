"""Step 8: the pgvector context store matches the in-memory projection.

These tests need a Postgres server with the pgvector extension. Set
TENET_PG_DSN (e.g. "host=/tmp/pg port=5432 user=tenet dbname=tenet_test") to
run them; without it they are skipped. The pushdown contract itself is also
covered without Postgres by test_retriever_pushdown.py.
"""

import os
import uuid

import pytest

from tenet import HashingEmbedder, MemoryCore
from tenet.events import SQLiteEventLog
from tenet.memory.models import ContextRecord
from tenet.scope import ScopeGrant

DSN = os.environ.get("TENET_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="TENET_PG_DSN not set")
DIM = 64


@pytest.fixture
def make_store():
    psycopg = pytest.importorskip("psycopg")
    from tenet.memory.pgvector import PgVectorContextStore
    tables, stores = [], []

    def make(table=None, dim=DIM):
        table = table or f"t_{uuid.uuid4().hex[:12]}"
        tables.append(table)
        store = PgVectorContextStore(DSN, dim=dim, table=table)
        stores.append(store)
        return store

    yield make
    for store in stores:
        store.close()
    with psycopg.connect(DSN, autocommit=True) as conn:
        for table in set(tables):
            conn.execute(f'DROP TABLE IF EXISTS "{table}"')


CORPUS = [
    ("alpha", "a/runbook.md", "The alpha project deploys to us-east-1 on Fridays."),
    ("alpha", "a/ops.md", "Alpha's database backups run nightly at 02:00 UTC."),
    ("beta", "b/runbook.md", "The beta project deploys to eu-west-1 and stores backups in Glacier."),
    ("alpha", "a/empty.md", "!!!"),  # tokenless: a zero embedding
]


def _grant(*namespaces, task="t"):
    return ScopeGrant.issue(principal="alice", task_id=task, namespaces=set(namespaces))


def _cores(store, log=None):
    embedder = HashingEmbedder(dim=DIM)
    reference = MemoryCore(embedder)
    pg = MemoryCore(embedder, context_store=store, log=log)
    for ns, source, text in CORPUS:
        reference.ingest(text, namespace=ns, source=source, ingest_id=source)
        pg.ingest(text, namespace=ns, source=source, ingest_id=source)
    return reference, pg


def _ranked(results):
    return [(r.record.source, round(r.score, 5)) for r in results]


@pytest.mark.parametrize("query", ["where does the project deploy?", "backups in Glacier", "!!!", ""])
def test_ranking_matches_in_memory_retriever(make_store, query):
    reference, pg = _cores(make_store())
    for namespaces in (("alpha",), ("alpha", "beta")):
        g = _grant(*namespaces)
        assert _ranked(pg.retrieve(query, g)) == _ranked(reference.retrieve(query, g))
        assert _ranked(pg.retrieve(query, g, limit=1)) == _ranked(reference.retrieve(query, g, limit=1))


def test_out_of_grant_namespace_never_enters_candidates(make_store):
    _, pg = _cores(make_store())
    alpha = _grant("alpha")
    assert pg.retrieve("backups in Glacier", alpha, namespace="beta") == []
    results = pg.retrieve("beta eu-west-1 Glacier", alpha, limit=10)
    assert results and {r.record.namespace for r in results} == {"alpha"}


def test_records_round_trip_exactly(make_store):
    store = make_store()
    _, pg = _cores(store)
    for record in store.list():
        assert store.get(record.context_id) == record
    original = next(r for r in store.list() if r.source == "a/runbook.md")
    assert pg.context(original.context_id) == original
    assert {r.namespace for r in store.list("beta")} == {"beta"}


def test_dimension_mismatch_is_rejected(make_store):
    store = make_store()
    record = ContextRecord("c", "r", "ns", "x", (1.0, 0.0), "v", "h", "s")
    with pytest.raises(ValueError, match="dimensions"):
        store.upsert(record)
    with pytest.raises(ValueError, match="vector"):
        make_store(table=store.table, dim=DIM * 2)


def test_failed_rebuild_rolls_back_and_publish_is_atomic(make_store):
    store = make_store()
    _, pg = _cores(store)
    before = _ranked(pg.retrieve("deploy", _grant("alpha", "beta")))

    class Exploding:
        version = "boom"
        def contextualize(self, raw):
            raise RuntimeError("contextualizer failed")

    pg._contextualizer = Exploding()
    with pytest.raises(RuntimeError):
        pg.rebuild_projections()
    assert _ranked(pg.retrieve("deploy", _grant("alpha", "beta"))) == before
    observer = make_store(table=store.table)
    assert len(observer.list()) == len(CORPUS)


def test_rebuild_publishes_to_other_readers(make_store):
    store = make_store()
    _, pg = _cores(store)
    observer = make_store(table=store.table)
    observer.clear()  # projection damaged out of band
    assert observer.list() == []
    pg.rebuild_projections()
    assert len(observer.list()) == len(CORPUS)


def test_reopen_hydrates_from_durable_log(make_store, tmp_path):
    store = make_store()
    log = SQLiteEventLog(tmp_path / "events.db")
    _, pg = _cores(store, log=log)
    expected = _ranked(pg.retrieve("deploy", _grant("alpha", "beta")))
    log.close()
    store.clear()

    reopened = SQLiteEventLog(tmp_path / "events.db")
    fresh = MemoryCore(HashingEmbedder(dim=DIM), log=reopened,
                       context_store=make_store(table=store.table))
    assert _ranked(fresh.retrieve("deploy", _grant("alpha", "beta"))) == expected
    reopened.close()


def test_headline_demo_blocks_with_pgvector_memory(make_store, tmp_path):
    from tenet.demo.corpus import SECRET, make_sandbox
    from tenet.demo.tenet_agent import run_tenet_agent
    from tenet.events.audit import audit_trace

    root = make_sandbox(tmp_path)
    store = make_store(dim=256)
    result, memory = run_tenet_agent(root, context_store=store)
    assert (root / SECRET).exists()
    assert [s.proposal.tool for s in result.steps] == ["fs.read", "fs.delete"]
    assert audit_trace(memory.log).issues == ()


def test_stale_table_is_cleared_for_an_empty_log(make_store):
    store = make_store()
    _cores(store)
    fresh = MemoryCore(HashingEmbedder(dim=DIM), context_store=make_store(table=store.table))
    assert fresh.retrieve("deploy", _grant("alpha", "beta")) == []
