"""The approver — the human (or stand-in) who decides escalations (§7).

The approval surface renders the proposal and the gate's rationale and NOTHING
ELSE — never memory content. An injected memory must not be able to phrase its
own approval request; the approver sees what the agent *wants to do* and what
the gate *thought about it*, not what the memory *said*. ``ApprovalSurface`` is
deliberately a closed set of fields so there is nowhere for content to ride in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ApprovalSurface:
    """Exactly what an approver may see. Built from the proposal + gate decision
    only; constructing one never touches the memory subsystem."""

    tool: str
    args: dict
    justification: str          # the Brain's stated reason (intent, not memory)
    gate_rationale: str         # why the gate escalated
    matched_rule_ids: tuple[str, ...]

    def render(self) -> str:
        """A human-readable rendering for CLI/log surfaces."""
        return (
            f"APPROVAL REQUESTED\n"
            f"  tool:          {self.tool}\n"
            f"  args:          {self.args}\n"
            f"  justification: {self.justification}\n"
            f"  gate says:     {self.gate_rationale} "
            f"(rules: {', '.join(self.matched_rule_ids) or '-'})"
        )


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    approver_id: str
    note: str = ""


@runtime_checkable
class Approver(Protocol):
    id: str

    def decide(self, surface: ApprovalSurface) -> ApprovalDecision: ...
