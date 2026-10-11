"""Event-backed memory with idempotent ingestion and atomic projection rebuilds."""

from __future__ import annotations

from threading import RLock
from ..values import utc_now
from ..events import Actor, InMemoryEventLog, EventLog, new_ulid
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


class ProjectionError(RuntimeError):
    """Raw data was accepted; retry the same ingest_id to finish projection."""
    def __init__(self, raw_id, ingest_id):
        super().__init__(f"projection failed for accepted raw record {raw_id}")
        self.raw_id, self.ingest_id = raw_id, ingest_id


class MemoryCore:
    def __init__(
        self,
        embedder: Embedder,
        *,
        log: EventLog | None = None,
        raw_store: RawStore | None = None,
        context_store: ContextStore | None = None,
        contextualizer: Contextualizer | None = None,
        clock=utc_now,
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
        self._owns_context = False  # True once the store is a replacement made here
        self._retriever = Retriever(self._context, embedder)
        self._embedder = embedder
        self._lock = RLock()
        self.clock = clock
        # A reused store (e.g. a pgvector table) must equal the fold of THIS
        # log; rows from another log would be unattributable.
        if len(self._log) or self._context.list():
            self.rebuild_projections()

    @property
    def log(self) -> EventLog:
        return self._log

    def ingest(
        self, content: str, namespace: str, source: str, *,
        media_type: str = "text/plain", actor: Actor | None = None,
        correlation_id: str | None = None, ingest_id: str | None = None,
    ) -> RawRecord:
        ingest_id = new_ulid() if ingest_id is None else ingest_id
        if not isinstance(ingest_id, str) or not ingest_id:
            raise ValueError("ingest_id must be a non-empty string")
        payload = dict(content=content, content_hash=content_hash(content),
                       source=source, media_type=media_type, ingest_id=ingest_id)
        with self._lock:
            with self._log.transaction():
                existing = [e for e in self._log if e.event_type == MEMORY_RAW_APPENDED
                            and e.namespace == namespace
                            and e.payload.get("ingest_id") == ingest_id]
                if existing:
                    event = existing[0]
                    if dict(event.payload) != payload:
                        raise ValueError("ingest_id already used for different content or metadata")
                else:
                    event = self._log.append(
                        namespace=namespace, actor=actor or Actor(kind="user", id="ingest"),
                        event_type=MEMORY_RAW_APPENDED, payload=payload,
                        correlation_id=correlation_id or new_ulid())
            raw = _raw_from_event(event)
            try:
                contexts = list(self._contextualizer.contextualize(raw))
                self._raw.add(raw)
                for ctx in contexts:
                    self._context.upsert(ctx)
            except Exception as exc:
                raise ProjectionError(raw.raw_id, ingest_id) from exc
            return raw

    def rebuild_projections(self) -> None:
        """Verify and build private replacements before publishing either store.

        Store adapters must provide empty() to create an unpublished projection.
        An adapter whose replacement lives outside this process (pgvector) may
        also provide publish() and discard(); publish runs only after the whole
        rebuild succeeded, discard on any failure. In-memory stores keep the old
        snapshot for existing external references; a shared database does not.
        """
        with self._lock, self._log.transaction():
            self._log.verify()
            raw_store, context_store = self._raw.empty(), self._context.empty()
            try:
                for event in self._log:
                    if event.event_type == MEMORY_RAW_APPENDED:
                        raw = _raw_from_event(event)
                        contexts = list(self._contextualizer.contextualize(raw))
                        raw_store.add(raw)
                        for ctx in contexts:
                            context_store.upsert(ctx)
                if hasattr(context_store, "publish"):
                    context_store.publish()
            except BaseException:
                if hasattr(context_store, "discard"):
                    context_store.discard()
                raise
            previous, owned = self._context, self._owns_context
            self._raw, self._context = raw_store, context_store
            self._owns_context = True
            if owned and hasattr(previous, "close"):
                previous.close()  # a replacement this core created; nobody else holds it
            self._retriever = Retriever(self._context, self._embedder)

    def retrieve(
        self,
        query: str,
        grant: "ScopeGrant",
        *,
        namespace: str | None = None,
        limit: int = 5,
        now=None,
    ) -> list[RetrievalResult]:
        """Scope-bound retrieval. Authority comes only from ``grant``: retrieval
        can never reach memory outside the grant's namespaces (§5). Pass
        ``namespace`` to target one granted namespace, or leave it None to search
        the union of all granted namespaces."""
        with self._lock:
            return self._retriever.retrieve(query, grant, namespace, limit,
                                            now=self.clock() if now is None else now)

    # -- direct projection access (read-only helpers) ----------------------
    def raw(self, raw_id: str) -> RawRecord | None:
        with self._lock:
            return self._raw.get(raw_id)

    def context(self, context_id: str) -> ContextRecord | None:
        with self._lock:
            return self._context.get(context_id)
