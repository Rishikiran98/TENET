"""Bound approval requests. Agent-controlled text remains untrusted data."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from ..values import freeze, canonical, digest


@dataclass(frozen=True)
class ApprovalSurface:
    tool: str
    args: dict
    justification: str
    gate_rationale: str
    matched_rule_ids: tuple[str, ...]
    task_id: str = ""
    grant_id: str = ""
    policy_version: str = ""
    expires_at: str | None = None
    proposal_ref: str = ""

    def __post_init__(self):
        object.__setattr__(self, 'args', freeze(self.args))
        object.__setattr__(self, 'matched_rule_ids', tuple(self.matched_rule_ids))

    @property
    def binding(self) -> str:
        return digest({name: getattr(self, name) for name in self.__dataclass_fields__})

    def render(self) -> str:
        return (
            "APPROVAL REQUESTED\n"
            f"  binding: {self.binding}\n"
            f"  task: {canonical(self.task_id)}\n"
            f"  grant: {canonical(self.grant_id)}\n"
            f"  policy: {canonical(self.policy_version)}\n"
            f"  expires: {canonical(self.expires_at)}\n"
            f"  tool (untrusted): {canonical(self.tool)}\n"
            f"  args (untrusted): {canonical(self.args)}\n"
            f"  justification (untrusted): {canonical(self.justification)}\n"
            f"  gate rationale: {canonical(self.gate_rationale)}\n"
            f"  rules: {canonical(self.matched_rule_ids)}"
        )


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    approver_id: str
    note: str = ""
    binding: str = ""

    def __post_init__(self):
        if type(self.approved) is not bool:
            raise ValueError("approved must be a boolean")


@runtime_checkable
class Approver(Protocol):
    id: str
    def decide(self, surface: ApprovalSurface) -> ApprovalDecision: ...
