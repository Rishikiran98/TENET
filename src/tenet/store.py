"""Storage for memory records.

`MemoryStore` is the interface. `InMemoryStore` is the v1 implementation — a
plain dict. pgvector comes later and implements the same interface, so nothing
above the store changes when we swap it in.

Note `list(scope=...)`. Scope is a first-class filter at the storage boundary,
not an afterthought bolted on later. The retriever relies on being able to ask
for *only* the records in a given scope — that is the foundation of the agent
never being able to reach memory it is not authorised for.
"""

from __future__ import annotations

from typing import Protocol

from .models import MemoryRecord


class MemoryStore(Protocol):
    def add(self, record: MemoryRecord) -> None:
        ...

    def get(self, record_id: str) -> MemoryRecord | None:
        ...

    def list(self, scope: str | None = None) -> list[MemoryRecord]:
        ...


class InMemoryStore:
    """Dict-backed store. Fine for development and the demo; not durable."""

    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}

    def add(self, record: MemoryRecord) -> None:
        self._records[record.id] = record

    def get(self, record_id: str) -> MemoryRecord | None:
        return self._records.get(record_id)

    def list(self, scope: str | None = None) -> list[MemoryRecord]:
        records = list(self._records.values())
        if scope is None:
            return records
        return [r for r in records if r.scope == scope]
