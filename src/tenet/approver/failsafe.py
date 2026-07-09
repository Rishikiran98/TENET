"""Fail-safe approver — denies everything. The default when no human is
reachable: an unanswerable escalation must fail closed, never open."""

from __future__ import annotations

from .protocol import ApprovalDecision, ApprovalSurface


class FailSafeDenyApprover:
    def __init__(self, approver_id: str = "failsafe-deny") -> None:
        self.id = approver_id

    def decide(self, surface: ApprovalSurface) -> ApprovalDecision:
        return ApprovalDecision(False, self.id, "no approver available → fail-safe deny")
