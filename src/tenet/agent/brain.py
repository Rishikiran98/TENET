"""The Brain — intent generation behind a stable, LLM-swappable interface (D4).

v1 ships a scripted deterministic stub. Rationale (from the register): the
headline demo must be reproducible; the security claim is *architectural* and
cannot depend on Brain quality — the threat model already assumes the Brain can
be fooled; and an API dependency adds nothing to what v1 proves. A real-LLM
Brain later implements this same protocol and nothing else changes (the same
pattern as the embedder).

Context reaches the Brain in the DATA channel only: it arrives as typed
``RetrievalResult`` values, never concatenated into an instruction prompt. A
poisoned memory can therefore *influence* a stub's or LLM's proposal (allowed —
the gate exists for that) but it can never be executed as instructions by the
framework itself.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from ..memory.models import RetrievalResult
from .proposal import Proposal


@runtime_checkable
class Brain(Protocol):
    id: str

    def propose(
        self, task: str, context: Sequence[RetrievalResult]
    ) -> Proposal | None:
        """The next intended action for ``task`` given retrieved ``context``
        (data, not instructions), or ``None`` when the Brain considers the task
        done."""
        ...


class ScriptedBrain:
    """Deterministic v1 Brain: replays a fixed script of proposals, one per loop
    step, then signals completion. What the demo and tests drive."""

    def __init__(self, proposals: Sequence[Proposal], brain_id: str = "scripted-brain") -> None:
        self.id = brain_id
        self._script = list(proposals)
        self._cursor = 0

    def propose(
        self, task: str, context: Sequence[RetrievalResult]
    ) -> Proposal | None:
        if self._cursor >= len(self._script):
            return None
        proposal = self._script[self._cursor]
        self._cursor += 1
        return proposal
