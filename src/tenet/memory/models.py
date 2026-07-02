"""Core data types for the memory subsystem, after the event-log refactor.

The old single ``MemoryRecord`` splits along the raw/derived seam the
architecture demands (§4):

- ``RawRecord`` — the canonical source of truth. It *is* the content of a
  ``memory.raw.appended`` event: exact bytes ingested, never mutated, with a
  content hash recorded at ingest. Non-disposable.
- ``ContextRecord`` — a *derived projection* of a raw record produced by the
  contextualizer. Disposable and rebuildable; it is the only tier retrieval
  touches. It carries its provenance (``raw_id``, ``raw_content_hash``,
  ``contextualizer_version``) as fields, not as a slogan.

``RetrievalResult`` pairs a context record with its similarity score, keeping
provenance attached all the way to the caller.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


def content_hash(text: str) -> str:
    """SHA-256 hex of the exact bytes ingested. Recorded at ingest so any later
    copy can be checked against the original."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RawRecord:
    """The immutable, canonical unit of memory — the payload of one
    ``memory.raw.appended`` event, indexed by that event's id."""

    raw_id: str            # the memory.raw.appended event_id
    namespace: str         # tenancy boundary (from the event envelope)
    content: str           # canonical source of truth, never mutated
    content_hash: str      # SHA-256 of content, recorded at ingest
    source: str            # provenance: where this came from
    media_type: str        # e.g. "text/plain"
    ts: str                # ISO-8601 UTC, the event's append time


@dataclass(frozen=True)
class ContextRecord:
    """A derived, disposable projection of a raw record: the retrievable unit.

    Deterministic given ``contextualizer_version`` + the embedder, so the whole
    context store is a pure projection of the log (D2) — no events of its own.
    """

    context_id: str               # stable id derived from raw_id + chunk index
    raw_id: str                   # provenance: which raw record this derives from
    namespace: str                # inherited from the raw record
    text: str                     # the (possibly chunked) derived text
    embedding: list[float]        # derived projection of `text`
    contextualizer_version: str   # which transform produced this
    raw_content_hash: str         # provenance: hash of the canonical raw content
    source: str                   # provenance: carried through from raw


@dataclass(frozen=True)
class RetrievalResult:
    """A context record returned from retrieval, paired with its score. Carrying
    the whole record keeps provenance attached to the caller (gate, audit)."""

    record: ContextRecord
    score: float
