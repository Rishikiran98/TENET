"""Tenet — a secure, event-sourced memory and governance layer for AI agents.

One append-only, hash-chained event log is the single source of truth
(``tenet.events``); memory is a set of projections folded from it
(``tenet.memory``). Governance (scope grants, the gate, the agent loop) builds
on top in later slices — see CLAUDE.md.

This top level re-exports the most-used memory names for convenience; the event
log lives under ``tenet.events``.
"""

from __future__ import annotations

from .memory import (
    ChunkEmbedContextualizer,
    ContextRecord,
    ContextStore,
    Contextualizer,
    Embedder,
    HashingEmbedder,
    InMemoryContextStore,
    InMemoryRawStore,
    MemoryCore,
    RawRecord,
    RawStore,
    RetrievalResult,
    Retriever,
    SentenceTransformerEmbedder,
    content_hash,
)

__version__ = "0.2.0"

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
