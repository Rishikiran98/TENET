"""Retrieval: given a query and a grant, return the most similar context the
grant permits the caller to read.

The ordering is deliberate and is a security boundary, not a performance choice
(§4.4). It is now the full two-stage filter the contract calls for:

    1. GRANT AUTHORIZATION. The grant decides which namespaces this retrieval may
       touch at all. A requested namespace outside the grant is denied here —
       nothing from it ever enters the candidate set. Authority flows only from
       the grant (§5, invariant 1); no memory content can widen it.
    2. NAMESPACE FILTER at the storage boundary — ask the store only for records
       in the permitted namespace(s).
    3. SIMILARITY RANKING within that filtered set, then top-k.

Doing any filter after ranking would leak the existence and ordering of memory
the caller may not read. We don't do that.

Per §5 invariant 3 the retriever enforces ``namespaces`` and nothing else; the
grant's tools, constraints, action budget, and expiry are the gate's to enforce.
"""

from __future__ import annotations

import numpy as np

from ..scope import ScopeGrant
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
    ) -> list[RetrievalResult]:
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
            record for ns in allowed for record in self._store.list(namespace=ns)
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
        scored.sort(key=lambda res: res.score, reverse=True)
        return scored[:limit]
