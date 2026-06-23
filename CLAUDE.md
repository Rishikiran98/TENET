# HIEROMEM

A semantic memory layer for AI agents. Store a memory (embed + persist) and recall relevant memories (embed query → similarity search → rank → return), scoped by namespace.

This file is the design contract for the v1 rebuild. It doubles as the Claude Code project context file: read it before making changes. The guiding rule is defensibility — every piece should be small enough that the author can explain and justify it cold. When in doubt, build less, not more.

## v1 scope (what we are building NOW)

In:

* `POST /memories` — write a memory, scoped by `namespace`.
* `POST /memories/search` — recall the top-k memories for a query, scoped by `namespace`.
* `GET /health` — liveness + store reachability.
* Three interfaces: `Embedder`, `MemoryStore`, `Retriever`.
* Two store backends: an in-memory store (runs with zero infra) and a Postgres + pgvector store (production path).
* Tests for the in-memory path and the API.

Explicitly OUT of v1 (these are the roadmap — do NOT build them yet):

* Redis / caching. Add only when a specific hot path is measured.
* Eviction / forgetting policies (Layer 3).
* Recency decay + importance weighting in ranking (Layer 2).
* Multi-agent sharing / access control (Layer 4).

Why out: shipping all of it at once recreates the original failure — more surface area than the author reasoned through. Each layer ships separately, small enough to fully own.

## Architecture

Three interfaces form a stable core. Everything advanced plugs into the `Retriever` seam without rewriting the core.

```
        ┌─────────────────────────────────────────┐
        │              FastAPI surface             │
        │   POST /memories   POST /memories/search │
        └───────────────────┬─────────────────────┘
                            │
                      ┌─────▼──────┐
                      │  Retriever │   embed → store.search → rank
                      └──┬──────┬──┘
                  embed  │      │  persist + vector search
                  ┌──────▼─┐  ┌─▼────────────┐
                  │Embedder│  │ MemoryStore  │
                  └────────┘  └──────────────┘
                  Hash /        InMemory /
                  SentenceTf    Postgres+pgvector
```

* Embedder — `text -> vector`. The `Retriever` depends on this abstraction, not on any specific model. Swappable.
* MemoryStore — persistence + cosine similarity search, namespace-filtered. The pgvector SQL lives here and ONLY here.
* Retriever — orchestration. `remember()` and `recall()`. The `_rank()` method is the seam where Layer 2 plugs in; in v1 it is a no-op (store returns cosine-sorted results).

This is dependency inversion: the interview answer to "how would you add eviction / decay / multi-agent?" is "each is a strategy that satisfies one contract" — because the contracts were designed first.

## The layers (roadmap, build one at a time)

* Layer 2 — ranking & decay. A `Ranker` composed into `Retriever`: `final = similarity × recency_decay × importance`. Pure, unit-testable.
* Layer 3 — eviction. A `RetentionPolicy` interface with `should_evict(memory, context)`. LRU, TTL, salience = three implementations.
* Layer 4 — multi-agent. Falls out of namespaces: `{agent_id}` for private, shared `{team_id}` for collective, access check at the API boundary.

## Stack decisions (the defensible rationale)

* Python + FastAPI — lingua franca for this domain; no language risk on top of a systems-design exercise.
* Postgres + pgvector — one datastore gives vector search + relational metadata + ACID, explainable in two sentences.
* No Redis in v1 — added only when a measured hot path justifies it. "Added a cache, measured 6x P99 on repeat retrievals" is defensible; "we use Redis" is not.
* In-memory store ships alongside Postgres — lets the whole API run with zero infra, and is the test backend. Proves the abstraction is real.
* Embedder is sync; Retriever offloads it via `asyncio.to_thread` so a blocking model call never stalls the event loop. (Be able to explain this.)

## Proposed layout

```
hieromem/
├── CLAUDE.md                 # this file
├── pyproject.toml
├── .env.example
├── docker-compose.yml        # postgres + pgvector for local dev
├── sql/001_init.sql          # schema (memories table, vector(384), hnsw index)
├── src/hieromem/
│   ├── __init__.py
│   ├── config.py             # pydantic-settings; STORE_BACKEND, EMBEDDER_BACKEND
│   ├── models.py             # Memory, MemoryCreate, SearchRequest, SearchResult
│   ├── embedder.py           # Embedder protocol + HashEmbedder + SentenceTransformerEmbedder
│   ├── store.py              # MemoryStore protocol + InMemoryMemoryStore + PostgresMemoryStore
│   ├── retriever.py          # Retriever: remember() / recall() / _rank()
│   ├── deps.py               # factory: read config -> wire embedder + store
│   └── api.py                # FastAPI app + routes + lifespan
└── tests/
    ├── conftest.py
    ├── test_retriever.py     # HashEmbedder + InMemoryStore; exact-recall + namespace isolation
    └── test_api.py           # TestClient over the in-memory backend
```

## Build sequence for Claude Code (do these as discrete steps, review each)

1. Scaffold `pyproject.toml`, `.env.example`, package skeleton. Verify import.
2. `models.py` — DONE below, paste as-is.
3. `embedder.py` — `Embedder` Protocol; `HashEmbedder` (deterministic, no deps, for tests/plumbing); `SentenceTransformerEmbedder` (lazy import, real semantics, dim 384).
4. `store.py` — `MemoryStore` Protocol (async `write`, `search`, `health`); `InMemoryMemoryStore` (numpy cosine); `PostgresMemoryStore` (psycopg3 async pool + pgvector; lazy-import the driver bits).
5. `retriever.py` — `Retriever(embedder, store)`; `remember(MemoryCreate)`, `recall(SearchRequest)`; `_rank()` is identity in v1 (the Layer 2 seam).
6. `config.py` + `deps.py` — wire backends from env.
7. `sql/001_init.sql` + `docker-compose.yml` for the Postgres path.
8. `api.py` — routes + lifespan that builds deps into `app.state`.
9. Tests — in-memory backend only. Assert exact recall scores ~1.0 and that a memory in namespace A never returns for namespace B.
10. `README.md` — purpose, run-with-zero-infra quickstart, the design decisions above (this is the interview-prep gold), and the layer roadmap.

Do these ONE AT A TIME and explain each before moving on. The goal is that you can defend every file, not that it ships fast.

## Already drafted — paste these verbatim

### src/hieromem/__init__.py

```python
"""HIEROMEM — a semantic memory layer for AI agents.

v1 core: store a memory (embed + persist) and recall relevant memories
(embed query -> similarity search -> rank -> return), scoped by namespace.

The design is built around three interfaces so that everything advanced
(ranking/decay, eviction, multi-agent sharing) plugs into a stable core
without rewriting it:

    Embedder      text  -> vector
    MemoryStore   persistence + vector similarity search
    Retriever     orchestration (embed -> search -> rank)
"""

__version__ = "1.0.0"
```

### src/hieromem/models.py

```python
"""Data models — the vocabulary of the system.

These are deliberately small. A `Memory` is the unit we store and recall;
`MemoryCreate` / `SearchRequest` are the API inputs; `SearchResult` pairs a
memory with the score it earned for a given query.

Note `importance`: it is captured and stored in v1 but NOT yet used in
ranking. It exists now because it is a data-model decision (cheap to add up
front, painful to backfill later). Layer 2 ranking will read it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemoryCreate(BaseModel):
    """Input for writing a memory."""

    namespace: str = Field(..., min_length=1, description="Scope key, e.g. an agent or user id.")
    text: str = Field(..., min_length=1, description="The content to remember.")
    metadata: dict = Field(default_factory=dict, description="Arbitrary structured context.")
    importance: float = Field(default=1.0, ge=0.0, description="Caller hint; reserved for Layer 2 ranking.")


class Memory(BaseModel):
    """A stored memory."""

    id: UUID = Field(default_factory=uuid4)
    namespace: str
    text: str
    metadata: dict = Field(default_factory=dict)
    importance: float = 1.0
    created_at: datetime = Field(default_factory=_utcnow)

    @classmethod
    def from_create(cls, create: MemoryCreate) -> "Memory":
        return cls(
            namespace=create.namespace,
            text=create.text,
            metadata=create.metadata,
            importance=create.importance,
        )


class SearchRequest(BaseModel):
    """Input for recalling memories."""

    namespace: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1)
    k: int = Field(default=5, ge=1, le=100, description="Max results to return.")


class SearchResult(BaseModel):
    """A memory and the similarity score it earned for a query."""

    memory: Memory
    score: float
```
