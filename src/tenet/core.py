"""MemoryCore is the single seam the rest of Tenet depends on.

The agent (built later) does not touch the embedder, store, or retriever
directly. It holds one `MemoryCore` and calls `ingest` / `retrieve`. That keeps
the governance gate's job clean: it sits between the agent and the *tools*, and
it can trust that everything coming out of `retrieve` is already scope-bound.

`ingest` is where the derived projection (embedding) is computed from the
canonical content. Embedding never happens anywhere else.
"""

from __future__ import annotations

import uuid

from .embedder import Embedder
from .models import MemoryRecord, RetrievalResult
from .retriever import Retriever
from .store import MemoryStore


class MemoryCore:
    def __init__(self, embedder: Embedder, store: MemoryStore) -> None:
        self._embedder = embedder
        self._store = store
        self._retriever = Retriever(store, embedder)

    def ingest(
        self,
        content: str,
        scope: str,
        source: str,
        record_id: str | None = None,
    ) -> MemoryRecord:
        record = MemoryRecord(
            id=record_id or str(uuid.uuid4()),
            content=content,
            scope=scope,
            source=source,
            embedding=self._embedder.embed(content),  # derived from canonical
        )
        self._store.add(record)
        return record

    def retrieve(
        self, query: str, scope: str, limit: int = 5
    ) -> list[RetrievalResult]:
        return self._retriever.retrieve(query, scope, limit)
