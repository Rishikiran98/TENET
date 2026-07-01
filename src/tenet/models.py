"""Core data types for Tenet's memory layer.

Design intent:
- `content` is the CANONICAL SOURCE OF TRUTH. It is the raw text exactly as it
  was ingested. We never mutate it. Everything else (the embedding) is a
  *derived projection* of this canonical value.
- Every record carries its own PROVENANCE (`source`, `created_at`) and its
  SCOPE (`scope`). These are not decoration: scope drives the security model
  (the agent may only ever retrieve within an authorised scope), and provenance
  is what lets a downstream answer or action be audited and defended.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class MemoryRecord:
    """A single unit of memory.

    The embedding is optional at construction time because it is *derived* —
    the MemoryCore computes it on ingest. The canonical `content` exists
    independently of whether it has been embedded yet.
    """

    id: str
    content: str                       # canonical source of truth (never mutated)
    scope: str                         # namespace this memory belongs to
    source: str                        # provenance: where this came from
    created_at: datetime = field(default_factory=_now)   # freshness signal
    embedding: list[float] | None = None                 # derived projection


@dataclass
class RetrievalResult:
    """A record returned from a retrieval, paired with its similarity score.

    Carrying the whole record (not just its text) keeps provenance attached all
    the way to the caller — the governance gate and any answer/action can cite
    exactly where the context came from.
    """

    record: MemoryRecord
    score: float
