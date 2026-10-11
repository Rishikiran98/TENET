"""A deterministic stand-in for a Brain that memory content can fool.

``GullibleBrain`` treats imperative phrases found in retrieved context as
instructions: every ``read <path>`` or ``delete <path>`` becomes a proposal. A
real LLM is not guaranteed to behave this way, but the threat model (§1) assumes
it can, and the demo needs that failure to be reproducible. Both the naive and
the Tenet agent drive this same Brain, so the only difference is governance.
"""

from __future__ import annotations

import re
from typing import Sequence

from ..contracts import Observation, Proposal
from ..memory.models import RetrievalResult
from ..values import digest

_INSTRUCTION = re.compile(r"\b(read|delete)\s+(/[A-Za-z0-9_./-]*[A-Za-z0-9_-])")
_TOOLS = {"read": "fs.read", "delete": "fs.delete"}


def instructions(context: Sequence[RetrievalResult]) -> list[Proposal]:
    """Every imperative in ``context``, in rank order, as a cited proposal."""
    found = []
    for result in context:
        record = result.record
        for verb, path in _INSTRUCTION.findall(record.text):
            found.append(Proposal(
                tool=_TOOLS[verb], args={"path": path},
                justification=f"memory record {record.source!r} says to {verb} {path}",
                cited_context_ids=(record.context_id,),
            ))
    return found


class GullibleBrain:
    """Proposes each instruction found in memory once, then signals done."""

    def __init__(self, brain_id: str = "gullible-brain") -> None:
        self.id = brain_id

    def propose(
        self, task: str, context: Sequence[RetrievalResult],
        *, observations: Sequence[Observation] = (),
    ) -> Proposal | None:
        attempted = {(o.proposal.tool, digest(o.proposal.args)) for o in observations}
        for proposal in instructions(context):
            if (proposal.tool, digest(proposal.args)) not in attempted:
                return proposal
        return None
