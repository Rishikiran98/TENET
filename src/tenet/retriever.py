"""Retrieval: given a query and a scope, return the most similar memories.

The ordering of operations matters and is deliberate:

    1. SCOPE FILTER FIRST. We ask the store only for records in the requested
       scope. Out-of-scope memories are never even ranked. This is a security
       boundary, not a performance optimisation — it means a query in scope A
       physically cannot surface a memory from scope B.
    2. THEN score by similarity within that filtered set.
    3. THEN take the top-k.

Doing scope after similarity (rank everything, then drop the wrong scope) would
leak the existence and ordering of out-of-scope data. We don't do that.
"""

from __future__ import annotations

import numpy as np

from .embedder import Embedder
from .models import RetrievalResult
from .store import MemoryStore


def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


class Retriever:
    def __init__(self, store: MemoryStore, embedder: Embedder) -> None:
        self._store = store
        self._embedder = embedder

    def retrieve(
        self, query: str, scope: str, limit: int = 5
    ) -> list[RetrievalResult]:
        # 1. scope filter first
        candidates = self._store.list(scope=scope)
        if not candidates:
            return []

        # 2. score within scope
        query_vec = self._embedder.embed(query)
        scored = [
            RetrievalResult(record=r, score=_cosine(query_vec, r.embedding))
            for r in candidates
            if r.embedding is not None
        ]

        # 3. top-k
        scored.sort(key=lambda res: res.score, reverse=True)
        return scored[:limit]
