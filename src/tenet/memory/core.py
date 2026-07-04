"""MemoryCore — the memory subsystem's seam, now sitting on the event log.

``ingest`` is the write path and follows the architecture exactly (§4.4):

    append `memory.raw.appended` event  →  project raw store  →  contextualize
    →  upsert context projection

The event log is the source of truth. The raw store and context store are
*projections*: ``rebuild_projections`` drops and re-folds them straight from the
log, which is both the correctness proof (retrieval after a rebuild is identical)
and the tamper-detection mechanism (§4.2 — re-derive over immutable raw and diff).

Per D2 the v1 contextualizer is deterministic, so context is a pure projection
and ``ingest`` emits exactly one event per record: the raw append. No
``memory.context.derived`` events.
"""

from __future__ import annotations

from ..events import Actor, InMemoryEventLog, EventLog, new_ulid, rebuild
from ..events.taxonomy import MEMORY_RAW_APPENDED
from ..scope import ScopeGrant
from .contextstore import ContextStore, InMemoryContextStore
from .contextualizer import ChunkEmbedContextualizer, Contextualizer
from .embedder import Embedder
from .models import ContextRecord, RawRecord, RetrievalResult, content_hash
from .rawstore import InMemoryRawStore, RawStore
from .retriever import Retriever


def _raw_from_event(event) -> RawRecord:
    """Rebuild a raw record from its ``memory.raw.appended`` event — the fold
    that makes the raw store a projection of the log."""
    p = event.payload
    return RawRecord(
        raw_id=event.event_id,
        namespace=event.namespace,
        content=p["content"],
        content_hash=p["content_hash"],
        source=p["source"],
        media_type=p.get("media_type", "text/plain"),
        ts=event.ts,
    )


class MemoryCore:
    def __init__(
        self,
        embedder: Embedder,
        *,
        log: EventLog | None = None,
        raw_store: RawStore | None = None,
        context_store: ContextStore | None = None,
        contextualizer: Contextualizer | None = None,
    ) -> None:
        # Explicit None checks, not `or`: an empty InMemoryEventLog is falsy
        # (it defines __len__), so `log or InMemoryEventLog()` would silently
        # discard a caller-supplied empty log and build a private one.
        self._log = log if log is not None else InMemoryEventLog()
        self._raw = raw_store if raw_store is not None else InMemoryRawStore()
        self._context = (
            context_store if context_store is not None else InMemoryContextStore()
        )
        self._contextualizer = (
            contextualizer
            if contextualizer is not None
            else ChunkEmbedContextualizer(embedder)
        )
        self._retriever = Retriever(self._context, embedder)

    @property
    def log(self) -> EventLog:
        return self._log

    def ingest(
        self,
        content: str,
        namespace: str,
        source: str,
        *,
        media_type: str = "text/plain",
        actor: Actor | None = None,
        correlation_id: str | None = None,
    ) -> RawRecord:
        # 1. append the raw event — the source of truth. Its id is the raw_id.
        event = self._log.append(
            namespace=namespace,
            actor=actor or Actor(kind="user", id="ingest"),
            event_type=MEMORY_RAW_APPENDED,
            payload={
                "content": content,
                "content_hash": content_hash(content),
                "source": source,
                "media_type": media_type,
            },
            correlation_id=correlation_id or new_ulid(),
        )
        # 2. project into the raw + context stores (same fold as a replay).
        raw = _raw_from_event(event)
        self._project(raw)
        return raw

    def _project(self, raw: RawRecord) -> None:
        """Apply one raw record to both projections. Ingest and replay share this
        so the live store and a rebuilt store can never diverge."""
        self._raw.add(raw)
        for ctx in self._contextualizer.contextualize(raw):
            self._context.upsert(ctx)

    def rebuild_projections(self) -> None:
        """Drop and re-fold the raw + context stores from the verified log. This
        is what makes them projections rather than authoritative state."""
        self._raw.clear()
        self._context.clear()

        def reducer(_state: None, event) -> None:
            if event.event_type == MEMORY_RAW_APPENDED:
                self._project(_raw_from_event(event))
            return None

        rebuild(self._log, reducer, None)  # verifies the hash chain, then folds

    def retrieve(
        self,
        query: str,
        grant: "ScopeGrant",
        *,
        namespace: str | None = None,
        limit: int = 5,
    ) -> list[RetrievalResult]:
        """Scope-bound retrieval. Authority comes only from ``grant``: retrieval
        can never reach memory outside the grant's namespaces (§5). Pass
        ``namespace`` to target one granted namespace, or leave it None to search
        the union of all granted namespaces."""
        return self._retriever.retrieve(query, grant, namespace, limit)

    # -- direct projection access (read-only helpers) ----------------------
    def raw(self, raw_id: str) -> RawRecord | None:
        return self._raw.get(raw_id)

    def context(self, context_id: str) -> ContextRecord | None:
        return self._context.get(context_id)
