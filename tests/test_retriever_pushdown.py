"""A store that ranks itself only ever sees namespaces the grant permits."""

from tenet import HashingEmbedder, MemoryCore
from tenet.memory import InMemoryContextStore
from tenet.memory.models import RetrievalResult
from tenet.scope import ScopeGrant


class SearchingStore(InMemoryContextStore):
    def __init__(self):
        super().__init__()
        self.calls = []

    def search(self, query_vec, namespaces, limit):
        self.calls.append((tuple(namespaces), limit))
        return [RetrievalResult(r, 1.0) for r in self.list() if r.namespace in namespaces][:limit]

    def empty(self):
        return SearchingStore()


def test_search_receives_only_granted_namespaces():
    store = SearchingStore()
    core = MemoryCore(HashingEmbedder(), context_store=store)
    core.ingest("alpha note", namespace="alpha", source="a")
    core.ingest("beta secret", namespace="beta", source="b")
    grant = ScopeGrant.issue(principal="p", task_id="t", namespaces={"alpha"})

    results = core.retrieve("secret", grant, limit=3)
    assert [r.record.namespace for r in results] == ["alpha"]
    assert core.retrieve("secret", grant, namespace="beta") == []
    assert store.calls == [(("alpha",), 3)]  # the denied call never reached the store


def test_expired_grant_never_reaches_store():
    store = SearchingStore()
    core = MemoryCore(HashingEmbedder(), context_store=store)
    grant = ScopeGrant.issue(principal="p", task_id="t", namespaces={"alpha"}, ttl_seconds=0)
    assert core.retrieve("anything", grant) == []
    assert store.calls == []
