"""Scripted approver — deterministic decisions for tests and the demo."""

from __future__ import annotations

from typing import Sequence

from .protocol import ApprovalDecision, ApprovalSurface


class ScriptedApprover:
    """Replays a fixed sequence of approve/reject decisions. Runs out of
    script ⇒ deny (fail-safe, never fail-open)."""

    def __init__(self, decisions: Sequence[bool], approver_id: str = "scripted-approver") -> None:
        self.id = approver_id
        self._script = list(decisions)
        self._cursor = 0

    def decide(self, surface: ApprovalSurface) -> ApprovalDecision:
        if self._cursor >= len(self._script):
            return ApprovalDecision(False, self.id, "script exhausted → fail-safe deny")
        approved = self._script[self._cursor]
        self._cursor += 1
        return ApprovalDecision(approved, self.id, "scripted decision")
