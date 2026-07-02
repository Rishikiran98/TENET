"""The context store — derived, disposable, the only tier retrieval touches (§4.4).

Every record here is a projection of a raw record produced by the
contextualizer. The store can be dropped and rebuilt at any time by folding
``memory.raw.appended`` events through the contextualizer (see ``memory/core.py``).

``list(namespace=...)`` is a first-class filter at the storage boundary — the
foundation of the retriever never being able to reach memory outside the
requested namespace. Scope/namespace filtering is a security boundary, so it
lives here, not bolted on after ranking.

``ContextStore`` is the interface; ``InMemoryContextStore`` is the v1 impl.
"""

from __future__ import annotations

from typing import Protocol

from .models import ContextRecord


class ContextStore(Protocol):
    def upsert(self, record: ContextRecord) -> None:
        ...

    def get(self, context_id: str) -> ContextRecord | None:
        ...

    def list(self, namespace: str | None = None) -> list[ContextRecord]:
        ...

    def clear(self) -> None:
        ...


class InMemoryContextStore:
    """Dict-backed context store. Disposable: rebuildable from the log."""

    def __init__(self) -> None:
        self._records: dict[str, ContextRecord] = {}

    def upsert(self, record: ContextRecord) -> None:
        # Deterministic context_ids mean re-deriving the same raw record
        # overwrites in place rather than duplicating — a projection, not a heap.
        self._records[record.context_id] = record

    def get(self, context_id: str) -> ContextRecord | None:
        return self._records.get(context_id)

    def list(self, namespace: str | None = None) -> list[ContextRecord]:
        records = list(self._records.values())
        if namespace is None:
            return records
        return [r for r in records if r.namespace == namespace]

    def clear(self) -> None:
        self._records.clear()
