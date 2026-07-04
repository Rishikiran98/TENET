"""Retrieval: given a query and a namespace, return the most similar context.

The ordering of operations is deliberate and is a security boundary, not a
performance choice (§4.4):

    1. NAMESPACE FILTER FIRST. Ask the context store only for records in the
       requested namespace. Out-of-namespace memory is never even scored — a
       query in namespace A physically cannot surface memory from namespace B.
    2. THEN score by similarity within that filtered set.
    3. THEN take the top-k.

Doing the filter after ranking (rank everything, then drop the wrong namespace)
would leak the existence and ordering of memory the caller may not read. We
don't do that.

The full §4.4 contract is a two-stage filter — the grant's allowed namespaces,
then the query's requested namespace. Grant enforcement is build step 3; this
retriever implements the requested-namespace stage and is shaped so the grant
stage slots in ahead of it without changing the ranking path.
"""

from __future__ import annotations

import numpy as np

from .contextstore import ContextStore
from .embedder import Embedder
from .models import RetrievalResult


def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


class Retriever:
    def __init__(self, store: ContextStore, embedder: Embedder) -> None:
        self._store = store
        self._embedder = embedder

    def retrieve(
        self, query: str, namespace: str, limit: int = 5
    ) -> list[RetrievalResult]:
        # 1. namespace filter first (the candidate set is already bounded)
        candidates = self._store.list(namespace=namespace)
        if not candidates:
            return []

        # 2. score within the namespace
        query_vec = self._embedder.embed(query)
        scored = [
            RetrievalResult(record=r, score=_cosine(query_vec, r.embedding))
            for r in candidates
            if r.embedding
        ]

        # 3. top-k
        scored.sort(key=lambda res: res.score, reverse=True)
        return scored[:limit]
