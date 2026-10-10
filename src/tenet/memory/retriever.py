"""Authorize before ranking; expiry also applies to reads."""
from __future__ import annotations

import numpy as np

from ..scope import ScopeGrant
from ..values import utc_now
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
        self,
        query: str,
        grant: ScopeGrant,
        namespace: str | None = None,
        limit: int = 5,
        *, now=None,
    ) -> list[RetrievalResult]:
        if type(limit) is not int or limit < 0:
            raise ValueError("limit must be a nonnegative integer")
        if grant.is_expired(utc_now() if now is None else now):
            return []
        # 1. grant authorization: which namespaces may this retrieval read?
        #    - namespace given  -> only if the grant permits it, else denied
        #    - namespace None   -> the union of the grant's namespaces
        if namespace is None:
            allowed = grant.namespaces
        elif grant.can_read(namespace):
            allowed = frozenset({namespace})
        else:
            return []  # out-of-grant namespace: denied, never scored
        if not allowed:
            return []

        # 2. namespace filter at the storage boundary
        candidates = [
            record for ns in sorted(allowed) for record in self._store.list(namespace=ns)
        ]
        if not candidates:
            return []

        # 3. rank within the permitted set
        query_vec = self._embedder.embed(query)
        scored = [
            RetrievalResult(record=r, score=_cosine(query_vec, r.embedding))
            for r in candidates
            if r.embedding
        ]
        scored.sort(key=lambda res: (-res.score, res.record.context_id))
        return scored[:limit]
