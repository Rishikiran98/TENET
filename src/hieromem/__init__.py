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
