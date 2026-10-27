"""``memory.write``: the agent's only way to persist memory (§7).

Agents earn writes; frameworks don't gift them. The tool exists only when the
deployment registers it, runs only after the grant and gate allowed the
proposal, and re-checks the constraints itself (defense in depth, §8).

What it writes is ordinary memory: a ``memory.raw.appended`` event, so the
context projection, replay and retrieval treat it like any other ingest. It is
still data, never authority (§5 invariant 1). Provenance is a field: the actor
is ``tool:memory.write``, the source is ``AGENT_SOURCE``, and the receipt names
the ``raw_id``, so the audit links each agent-written record to the proposal,
verdict and grant that allowed it.
"""

from __future__ import annotations

from ..constraints import ConstraintViolation, check_constraints
from ..contracts import ToolExecutionError
from ..events import Actor
from ..memory import MemoryCore

AGENT_SOURCE = "agent:memory.write"


class MemoryWrite:
    name = "memory.write"

    def __init__(self, memory: MemoryCore) -> None:
        self._memory = memory

    def execute(self, args: dict, constraints: dict) -> dict:
        try:
            check_constraints(self.name, args, constraints)
        except ConstraintViolation as exc:
            raise ToolExecutionError(str(exc)) from exc
        raw = self._memory.ingest(args["content"], namespace=args["namespace"],
                                  source=AGENT_SOURCE,
                                  actor=Actor(kind="tool", id=self.name))
        # The content is already in the log as the memory itself; the receipt
        # carries only where it went and what it hashes to.
        return {"namespace": raw.namespace, "raw_id": raw.raw_id,
                "content_hash": raw.content_hash,
                "bytes_written": len(args["content"].encode("utf-8"))}
