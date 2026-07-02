"""The event envelope (D1) — the shape every event shares.

Two design points worth defending:

- **ULID event ids.** Lexically sortable (timestamp in the high-order bits) and
  globally unique, so append order and sort order agree without a separate
  sequence column.
- **Hash chain.** ``hash`` is SHA-256 over a canonical serialization of every
  other envelope field, including ``prev_hash``. Any mutation of a past event
  changes its hash, which breaks every link after it — the log is tamper-evident
  for the cost of one hash per append (D6).

The canonical serialization is deterministic (sorted keys, no whitespace) so the
same event always hashes to the same value. Payloads must therefore be
JSON-serializable; non-deterministic content has no place in a hash chain.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

# Crockford base32 (no I, L, O, U) — the ULID alphabet.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# The first event links to this. 64 zeros = "no prior event".
GENESIS_HASH = "0" * 64


def new_ulid() -> str:
    """A 26-char ULID: 48-bit millisecond timestamp + 80 bits of randomness,
    encoded in Crockford base32. Sortable by creation time because the timestamp
    occupies the most-significant bits."""
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    value = (ms << 80) | int.from_bytes(os.urandom(10), "big")
    out = bytearray(26)
    for i in range(25, -1, -1):
        out[i] = ord(_CROCKFORD[value & 0x1F])
        value >>= 5
    return out.decode("ascii")


def now_iso() -> str:
    """ISO-8601 UTC timestamp, assigned at append."""
    return datetime.now(timezone.utc).isoformat()


ActorKind = Literal["user", "agent", "gate", "approver", "system", "tool"]


@dataclass(frozen=True)
class Actor:
    """Who caused an event. Immutable, like the event it rides on."""

    kind: ActorKind
    id: str


@dataclass(frozen=True)
class Event:
    """One entry in the log. Immutable and never deleted; corrections are new
    events. ``causation_id`` gives the why-chain (a verdict caused by a proposal
    caused by a retrieval caused by a task); ``correlation_id`` groups one task
    end to end."""

    event_id: str            # ULID — lexically sortable, globally unique
    ts: str                  # ISO-8601 UTC, assigned at append
    namespace: str           # tenancy boundary, present on every event
    actor: Actor             # who caused it
    event_type: str          # from the closed taxonomy
    payload: dict            # type-specific, schema-versioned, JSON-serializable
    schema_version: int      # of this event type's payload
    correlation_id: str      # task_id — groups one agent task end to end
    causation_id: str | None  # event_id that directly caused this one
    prev_hash: str           # hash of the previous event (chain)
    hash: str                # SHA-256 over the canonical form of all the above


def _material(
    *,
    event_id: str,
    ts: str,
    namespace: str,
    actor: Actor,
    event_type: str,
    payload: dict,
    schema_version: int,
    correlation_id: str,
    causation_id: str | None,
    prev_hash: str,
) -> bytes:
    """Canonical bytes that get hashed: every envelope field EXCEPT ``hash``,
    serialized deterministically. Raises if the payload is not JSON-serializable
    — an unhashable payload must never enter the chain."""
    doc = {
        "event_id": event_id,
        "ts": ts,
        "namespace": namespace,
        "actor": {"kind": actor.kind, "id": actor.id},
        "event_type": event_type,
        "payload": payload,
        "schema_version": schema_version,
        "correlation_id": correlation_id,
        "causation_id": causation_id,
        "prev_hash": prev_hash,
    }
    return json.dumps(
        doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def compute_hash(**fields) -> str:
    """SHA-256 of the canonical material for a not-yet-sealed event."""
    return hashlib.sha256(_material(**fields)).hexdigest()


def event_hash(event: Event) -> str:
    """Recompute an existing event's hash from its own fields. Used to verify the
    chain: if this does not equal ``event.hash``, the event was tampered with."""
    return compute_hash(
        event_id=event.event_id,
        ts=event.ts,
        namespace=event.namespace,
        actor=event.actor,
        event_type=event.event_type,
        payload=event.payload,
        schema_version=event.schema_version,
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
        prev_hash=event.prev_hash,
    )
