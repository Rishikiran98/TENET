"""Tenet — a scope-aware semantic memory layer for AI agents.

Slice 1 (this package): store a memory (embed + persist) and retrieve the most
relevant memories for a query, strictly scoped by a namespace. This is the
foundation the governance gate and the secure action agent are built on later.

The design is organised around three swappable interfaces and one seam:

    Embedder      text  -> vector          (derived projection of content)
    MemoryStore   persistence + scoped listing
    Retriever     scope-filter-first similarity search
    MemoryCore    the single seam the rest of Tenet depends on

Nothing above the interfaces knows which concrete embedder or store is wired
in, so swapping ``HashingEmbedder`` for ``SentenceTransformerEmbedder`` (or
``InMemoryStore`` for pgvector) changes one line of wiring and nothing else.
"""

from __future__ import annotations

from .core import MemoryCore
from .embedder import Embedder, HashingEmbedder, SentenceTransformerEmbedder
from .models import MemoryRecord, RetrievalResult
from .retriever import Retriever
from .store import InMemoryStore, MemoryStore

__version__ = "0.1.0"

__all__ = [
    "MemoryCore",
    "Embedder",
    "HashingEmbedder",
    "SentenceTransformerEmbedder",
    "MemoryRecord",
    "RetrievalResult",
    "Retriever",
    "InMemoryStore",
    "MemoryStore",
]
