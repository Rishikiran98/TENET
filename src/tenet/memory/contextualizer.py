"""The contextualizer — the transform from raw to derived context (§4.2, §4.3).

It treats raw content strictly as **data**. This is the moved injection
frontier: if a contextualizer ever uses an LLM, a poisoned raw entry could
corrupt derived context that everything downstream trusts, so the
data/instruction separation must hold here too.

The determinism rule (D2):

    Deterministic transforms are projections. Non-deterministic transforms are
    events.

v1's contextualizer chunks and embeds with a pinned version, so it is
deterministic given (version, embedder). Its output is therefore a *pure
projection* of the log — it emits no ``memory.context.derived`` events. The
moment a contextualizer becomes non-deterministic, its outputs must instead be
appended as events, or replay can no longer reproduce history.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .embedder import Embedder
from .models import ContextRecord, RawRecord


@runtime_checkable
class Contextualizer(Protocol):
    version: str

    def contextualize(self, raw: RawRecord) -> list[ContextRecord]:
        ...


class ChunkEmbedContextualizer:
    """v1 contextualizer: one chunk per record (no splitting yet), embedded with
    a pinned embedder. Deterministic given ``version`` + the embedder, so its
    output is a pure projection (D2) — no events emitted.

    ``version`` folds in the embedder so that swapping the embedder is a visible
    version change: re-derive, and the ``contextualizer_version`` on every row
    tells you which transform produced it.
    """

    def __init__(self, embedder: Embedder, version: str = "chunk-embed/v1") -> None:
        self._embedder = embedder
        self.version = f"{version}+{type(embedder).__name__}(dim={embedder.dim})"

    def contextualize(self, raw: RawRecord) -> list[ContextRecord]:
        # v1 keeps the whole content as a single chunk. Real chunking (windows,
        # overlap) is a later upgrade and stays deterministic, so it remains a
        # projection — the shape here does not change.
        text = raw.content
        return [
            ContextRecord(
                context_id=f"{raw.raw_id}:0",
                raw_id=raw.raw_id,
                namespace=raw.namespace,
                text=text,
                embedding=self._embedder.embed(text),
                contextualizer_version=self.version,
                raw_content_hash=raw.content_hash,
                source=raw.source,
            )
        ]
