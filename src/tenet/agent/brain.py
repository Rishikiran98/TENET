"""Scripted intent generation using task text and retrieved context.

The current protocol does not receive tool or block outcomes. General multi-step
LLM use needs an observation-history extension before interchangeability can be
claimed. Retrieved content remains untrusted data, even in typed records.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from ..memory.models import RetrievalResult
from ..contracts import Proposal


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
