"""Tenet's event log — the append-only, hash-chained source of truth.

Public surface for build step 1. Memory, audit, and step history become folds
over this log in later slices; nothing in Tenet is authoritative except the log.
"""

from __future__ import annotations

from .envelope import (
    Actor,
    Event,
    GENESIS_HASH,
    compute_hash,
    event_hash,
    new_ulid,
    now_iso,
)
from .log import ChainError, EventLog, InMemoryEventLog
from .replay import Reducer, rebuild, replay
from . import taxonomy

__all__ = [
    "Actor",
    "Event",
    "GENESIS_HASH",
    "new_ulid",
    "now_iso",
    "compute_hash",
    "event_hash",
    "EventLog",
    "InMemoryEventLog",
    "ChainError",
    "replay",
    "rebuild",
    "Reducer",
    "taxonomy",
]
