"""Tenet's memory subsystem — sitting on the event log (build step 2).

Write path: ``MemoryCore.ingest`` appends a ``memory.raw.appended`` event (the
source of truth) and folds it into the raw store and the context store. The
context store is a pure projection (D2), rebuildable from the log.

Read path: ``MemoryCore.retrieve`` runs namespace-filter-first similarity search
over the context store — the only tier retrieval touches.
"""

from __future__ import annotations

from .contextstore import ContextStore, InMemoryContextStore
from .contextualizer import ChunkEmbedContextualizer, Contextualizer
from .core import MemoryCore
from .embedder import Embedder, HashingEmbedder, SentenceTransformerEmbedder
from .models import ContextRecord, RawRecord, RetrievalResult, content_hash
from .rawstore import InMemoryRawStore, RawStore
from .retriever import Retriever

__all__ = [
    "MemoryCore",
    "Embedder",
    "HashingEmbedder",
    "SentenceTransformerEmbedder",
    "Contextualizer",
    "ChunkEmbedContextualizer",
    "RawStore",
    "InMemoryRawStore",
    "ContextStore",
    "InMemoryContextStore",
    "Retriever",
    "RawRecord",
    "ContextRecord",
    "RetrievalResult",
    "content_hash",
]
