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


class InMemoryEventLog:
    """Dict/list-backed append-only log. Fine for development and the demo; the
    Postgres implementation swaps in behind the same interface."""

    def __init__(self) -> None:
        self._events: list[Event] = []

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
        # Every event carries a namespace (tenancy boundary) and a type from the
        # closed taxonomy with a well-formed payload. Reject at the boundary.
        if not namespace:
            raise ValueError("every event must carry a namespace")
        validate_payload(event_type, payload)

        prev_hash = self._events[-1].hash if self._events else GENESIS_HASH
        fields = dict(
            event_id=new_ulid(),
            ts=now_iso(),
            namespace=namespace,
            actor=actor,
            event_type=event_type,
            payload=payload,
            schema_version=schema_version,
            correlation_id=correlation_id,
            causation_id=causation_id,
            prev_hash=prev_hash,
        )
        event = Event(**fields, hash=compute_hash(**fields))
        self._events.append(event)
        return event

    def __iter__(self) -> Iterator[Event]:
        return iter(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def head_hash(self) -> str:
        """Hash of the most recent event, or GENESIS if the log is empty."""
        return self._events[-1].hash if self._events else GENESIS_HASH

    def verify(self) -> bool:
        """Walk the chain from genesis; raise ``ChainError`` on the first broken
        link or tampered event. Returns True if the whole log verifies."""
        prev = GENESIS_HASH
        for event in self._events:
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
