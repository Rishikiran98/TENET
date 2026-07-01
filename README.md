# Tenet — Memory Core (v1, slice 1)

The first slice of Tenet: a scope-aware semantic memory layer. This is the
foundation the governance gate and the secure action agent get built on top of
later. **Nothing here takes actions yet** — that's deliberate.

## Run it

The package lives under `src/` (src layout). Install it editable once, then the
demo and tests run from anywhere:

```bash
pip install -e ".[dev]"   # numpy + pytest
python -m tenet.demo      # see ranking + scope isolation
pytest                    # verify the invariants
```

Prefer not to install? Point Python at `src` directly:

```bash
pip install numpy pytest
PYTHONPATH=src python -m tenet.demo
pytest                    # pyproject already puts src/ on the path for tests
```

## What's in here

```
src/tenet/
  models.py     MemoryRecord (canonical source of truth), RetrievalResult
  embedder.py   Embedder interface + HashingEmbedder (default) + SentenceTransformer
  store.py      MemoryStore interface + InMemoryStore
  retriever.py  scope-filtered semantic search
  core.py       MemoryCore — the single seam everything else depends on
  demo.py       runnable walkthrough
tests/
  test_memory_core.py
```

## Three design decisions worth understanding (so you can defend them)

1. **Canonical content vs. derived projection.** `MemoryRecord.content` is the
   raw, never-mutated source of truth. The embedding is *derived* from it on
   ingest and computed in exactly one place (`MemoryCore.ingest`). Everything
   else treats the embedding as disposable.

2. **Scope filter happens first, in the store — not after ranking.** `Retriever`
   asks the store for records in the requested scope, *then* ranks. Out-of-scope
   memories are never scored. This is a security boundary: a query in scope A
   physically cannot surface scope B. `test_scope_isolation...` pins it.

3. **Everything depends on `MemoryCore`, not on a model.** The embedder and store
   are interfaces. Swapping `HashingEmbedder` for `SentenceTransformerEmbedder`,
   or `InMemoryStore` for pgvector, changes one line of wiring and nothing else.

## On the default embedder

`HashingEmbedder` is deterministic and needs no model download, so the demo and
tests run offline. It captures word overlap, not deep meaning. For real semantic
similarity, install `sentence-transformers` (`pip install ".[embeddings]"`) and
swap in `SentenceTransformerEmbedder` — same interface, one-line change.

## Not here yet (next slices, in order)

- **Tool layer** — sandboxed file ops (read/write/move/delete).
- **Governance gate** — the crown jewel. Sits between agent intent and tool
  execution. We slow down and build this carefully.
- **Agent loop** — task -> retrieve -> propose -> gate -> execute/block -> log.
- **Injection / scope demo** — poisoned memory vs. naive RAG.
- Then pgvector, then FastAPI.
