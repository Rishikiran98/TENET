# TENET

A scope-aware memory and governance layer for AI agents. Tenet gives an agent a
memory it can only ever read within an authorised scope, and — in later slices —
a governance gate that sits between the agent's intent and any real-world action.

This file is the design contract. It doubles as the Claude Code project context
file: read it before making changes. The guiding rule is **defensibility** —
every piece should be small enough that the author can explain and justify it
cold. When in doubt, build less, not more. We ship one slice at a time and can
defend every file in it before moving on.

## Where we are

**Slice 1 — the memory core — is built** (`src/tenet/`). Nothing in Tenet takes
actions yet; that is deliberate. The memory core is the foundation everything
else rests on, so it ships first and ships small.

## The core idea

An agent's memory is a security surface, not just a convenience. If a query in
one scope can surface another scope's memory — or if poisoned content can steer
an action — the agent is unsafe no matter how good its model is. Tenet makes the
memory scope a hard boundary first, then (later) puts a gate in front of actions.

## Architecture (slice 1, built)

Three swappable interfaces sit behind one seam. Everything above the interfaces
depends on `MemoryCore`, never on a concrete model or store.

```
                        ┌──────────────┐
                        │  MemoryCore  │   ingest() / retrieve()
                        └───┬──────┬───┘   the single seam
                  embed on  │      │  scope-filter-first search
                   ingest   │      │
                    ┌───────▼─┐  ┌─▼──────────┐  ┌────────────┐
                    │Embedder │  │ MemoryStore│  │ Retriever  │
                    └─────────┘  └────────────┘  └────────────┘
                    Hashing /     InMemoryStore   scope filter,
                    SentenceTf    (pgvector next)  then cosine, top-k
```

* **Embedder** — `text -> vector`. The embedding is a *derived projection* of
  canonical content, never authoritative. `HashingEmbedder` (default,
  deterministic, no downloads) and `SentenceTransformerEmbedder` (real
  semantics, one-line swap) both satisfy the interface; nothing else knows which
  is wired in.
* **MemoryStore** — persistence + **scoped listing**. `list(scope=...)` is a
  first-class filter at the storage boundary, not an afterthought. `InMemoryStore`
  is the v1 implementation; pgvector implements the same interface later.
* **Retriever** — scope-filter-**first** similarity search. It asks the store for
  records in the requested scope, *then* ranks. Out-of-scope memories are never
  scored.
* **MemoryCore** — the single seam the rest of Tenet depends on. `ingest()`
  computes the derived embedding from canonical content in exactly one place;
  `retrieve()` returns results that are already scope-bound.

## Three design decisions worth understanding (so you can defend them)

1. **Canonical content vs. derived projection.** `MemoryRecord.content` is the
   raw, never-mutated source of truth. The embedding is derived from it on
   ingest and computed in exactly one place (`MemoryCore.ingest`). Everything
   else treats the embedding as disposable. Provenance (`source`, `created_at`)
   travels with the record all the way to the caller so any answer or action can
   be audited.

2. **Scope filter happens first, in the store — not after ranking.** Doing scope
   after similarity (rank everything, then drop the wrong scope) would leak the
   existence and ordering of out-of-scope data. We filter first: a query in
   scope A physically cannot surface a memory from scope B. This is a security
   boundary, and `test_scope_isolation_blocks_cross_scope_retrieval` pins it as
   an executable assertion.

3. **Everything depends on `MemoryCore`, not on a model.** The embedder and store
   are interfaces. Swapping `HashingEmbedder` for `SentenceTransformerEmbedder`,
   or `InMemoryStore` for pgvector, changes one line of wiring and nothing else.
   This is dependency inversion: "how would you add real embeddings / pgvector /
   a gate?" is answered by "each satisfies a contract that already exists."

## Layout

```
tenet/
├── CLAUDE.md                 # this file — the design contract
├── pyproject.toml            # project = tenet; core dep is numpy only
├── requirements.txt          # slice-1 runtime (numpy)
├── README.md                 # quickstart + design rationale + roadmap
├── src/tenet/
│   ├── __init__.py           # public API surface
│   ├── models.py             # MemoryRecord (canonical), RetrievalResult
│   ├── embedder.py           # Embedder + HashingEmbedder + SentenceTransformerEmbedder
│   ├── store.py              # MemoryStore + InMemoryStore
│   ├── retriever.py          # scope-filter-first similarity search
│   ├── core.py               # MemoryCore — the single seam
│   └── demo.py               # runnable walkthrough (ranking + scope isolation)
└── tests/
    └── test_memory_core.py   # ranking, scope isolation, provenance/embedding
```

## Stack decisions (the defensible rationale)

* **Python** — lingua franca for this domain; no language risk on top of a
  systems-design exercise.
* **numpy is the only slice-1 dependency.** The core is plain dataclasses plus
  cosine similarity. Heavier things (sentence-transformers, Postgres, FastAPI)
  are opt-in extras in `pyproject.toml`, added only when a slice needs them —
  never up front.
* **`HashingEmbedder` is the default** so the demo and tests run offline with no
  model download. It captures lexical overlap, not deep meaning; swap in
  `SentenceTransformerEmbedder` (`pip install ".[embeddings]"`) for real
  semantics — same interface.
* **`InMemoryStore` ships first**; pgvector implements the same `MemoryStore`
  interface later. The in-memory store proves the abstraction is real and keeps
  the whole thing runnable with zero infra.

## Roadmap — build one slice at a time, in order

Each slice is small enough to own completely. Do NOT build ahead of this list.

1. **Tool layer** — sandboxed file ops (read / write / move / delete). The first
   things the agent can actually *do*.
2. **Governance gate** — the crown jewel. Sits between agent intent and tool
   execution: it inspects a proposed action against policy and scope and either
   allows, blocks, or escalates. We slow down and build this carefully.
3. **Agent loop** — task → retrieve → propose → gate → execute/block → log. Ties
   memory, tools, and the gate together into one auditable cycle.
4. **Injection / scope demo** — a poisoned memory vs. naive RAG, showing the gate
   and scope boundary defeating an attack a plain retriever would fall for.
5. **pgvector store** — the production `MemoryStore` behind the same interface.
6. **FastAPI surface** — HTTP endpoints over the core, once there is something
   worth exposing.

## Build discipline for Claude Code

* Work one slice at a time. Explain and justify a slice before moving to the next.
* Keep the three interfaces stable; new capability plugs into a contract that
  already exists rather than rewriting the core.
* Every new behaviour that matters gets an executable assertion — especially
  anything on the security boundary (scope isolation, and later the gate).
* Prefer building less. A smaller surface you can fully defend beats a larger one
  you cannot.

## Public API (slice 1)

```python
from tenet import MemoryCore, HashingEmbedder, InMemoryStore

core = MemoryCore(embedder=HashingEmbedder(), store=InMemoryStore())
core.ingest("deploys to us-east-1 on Fridays", scope="project-alpha", source="runbook.md")
results = core.retrieve("where does it deploy?", scope="project-alpha")  # scope-bound
```

Also exported: `Embedder`, `MemoryStore`, `Retriever`, `MemoryRecord`,
`RetrievalResult`, `SentenceTransformerEmbedder`.
