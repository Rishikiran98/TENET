"""The raw store — the memory subsystem's source of truth (§4.1).

A raw record is the content of a ``memory.raw.appended`` event: the exact bytes
ingested, untouched forever. The store indexes those records by id. It is
*non-disposable* in intent — you never throw raw away — but because the content
lives in the log, the store is still rebuildable by folding the log (see
``memory/core.py``). Nothing here mutates content; corrections are new events.

``RawStore`` is the interface; ``InMemoryRawStore`` is the v1 implementation.
"""

from __future__ import annotations

from typing import Protocol

from .models import RawRecord


class RawStore(Protocol):
    def add(self, record: RawRecord) -> None:
        ...

    def get(self, raw_id: str) -> RawRecord | None:
        ...

    def list(self, namespace: str | None = None) -> list[RawRecord]:
        ...

    def clear(self) -> None:
        ...


class InMemoryRawStore:
    """Dict-backed raw store. Fine for development and the demo; not durable."""

    def __init__(self) -> None:
        self._records: dict[str, RawRecord] = {}

    def add(self, record: RawRecord) -> None:
        # Append-only in spirit: a raw_id is an event id, so it never collides,
        # and re-adding the same id is idempotent (same immutable bytes).
        self._records[record.raw_id] = record

    def get(self, raw_id: str) -> RawRecord | None:
        return self._records.get(raw_id)

    def list(self, namespace: str | None = None) -> list[RawRecord]:
        records = list(self._records.values())
        if namespace is None:
            return records
        return [r for r in records if r.namespace == namespace]

    def clear(self) -> None:
        self._records.clear()
