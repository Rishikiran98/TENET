"""The append-only, hash-chained event log — Tenet's single source of truth.

Everything else (context store, audit view, step history) is a fold over this
log, not a separate system. The log therefore has exactly two jobs: append
validated events, and prove it has not been tampered with.

``EventLog`` is the interface; ``InMemoryEventLog`` is the v1 implementation. A
Postgres-backed log implements the same interface later (an ``events`` table
with a ULID primary key and hash columns) and nothing above it changes.
"""

from __future__ import annotations

from typing import Iterator, Protocol, runtime_checkable
from contextlib import contextmanager
from threading import RLock
from ..values import freeze

from .envelope import (
    Actor,
    Event,
    GENESIS_HASH,
    compute_hash,
    event_hash,
    new_ulid,
    now_iso,
)
from .taxonomy import validate_payload


class ChainError(Exception):
    """The hash chain does not verify — the log has been tampered with."""


@runtime_checkable
class EventLog(Protocol):
    def append(
        self,
        *,
        namespace: str,
        actor: Actor,
        event_type: str,
        payload: dict,
        correlation_id: str,
        causation_id: str | None = None,
        schema_version: int = 1,
    ) -> Event: ...

    def __iter__(self) -> Iterator[Event]: ...

    def __len__(self) -> int: ...

    def head_hash(self) -> str: ...

    def verify(self) -> bool: ...

    def transaction(self): ...


class InMemoryEventLog:
    """Thread-safe, process-local log. Use SQLiteEventLog for restart durability."""

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._lock = RLock()

    @contextmanager
    def transaction(self):
        """Serialize a read/check/append unit; roll back new events on error."""
        with self._lock:
            start = len(self._events)
            try:
                yield
            except BaseException:
                del self._events[start:]
                raise

    def _persist(self, event):
        """Durable implementations persist before publishing to readers."""

    def append(
        self,
        *,
        namespace: str,
        actor: Actor,
        event_type: str,
        payload: dict,
        correlation_id: str,
        causation_id: str | None = None,
        schema_version: int = 1,
    ) -> Event:
        with self.transaction():
            if not isinstance(namespace, str) or not namespace:
                raise ValueError("every event must carry a namespace")
            if not isinstance(correlation_id, str) or not correlation_id:
                raise ValueError("correlation_id must be non-empty")
            if type(schema_version) is not int or schema_version != 1:
                raise ValueError("unsupported schema_version")
            if not isinstance(actor, Actor):
                raise ValueError("invalid actor")
            payload = freeze(payload)
            validate_payload(event_type, payload)
            if causation_id is not None and not any(e.event_id == causation_id for e in self._events):
                raise ValueError("unknown causation_id")
            fields = dict(event_id=new_ulid(), ts=now_iso(), namespace=namespace,
                          actor=actor, event_type=event_type, payload=payload,
                          schema_version=schema_version, correlation_id=correlation_id,
                          causation_id=causation_id, prev_hash=self.head_hash())
            event = Event(**fields, hash=compute_hash(**fields))
            self._persist(event)
            self._events.append(event)
            return event

    def __iter__(self) -> Iterator[Event]:
        with self._lock:
            return iter(tuple(self._events))

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)

    def head_hash(self) -> str:
        """Hash of the most recent event, or GENESIS if the log is empty."""
        with self._lock:
            return self._events[-1].hash if self._events else GENESIS_HASH

    def verify(self) -> bool:
        """Walk the chain from genesis; raise ``ChainError`` on the first broken
        link or tampered event. Returns True if the whole log verifies."""
        prev = GENESIS_HASH
        for event in self:
            if event.prev_hash != prev:
                raise ChainError(
                    f"broken link at {event.event_id}: prev_hash does not match "
                    f"the preceding event"
                )
            if event_hash(event) != event.hash:
                raise ChainError(
                    f"tampered event {event.event_id}: recomputed hash does not "
                    f"match stored hash"
                )
            prev = event.hash
        return True
