"""CLI approver — prompts a human on stdin. Interactive; not exercised by the
test suite (the scripted and fail-safe approvers cover the contract)."""

from __future__ import annotations

from .protocol import ApprovalDecision, ApprovalSurface


class CLIApprover:  # pragma: no cover — interactive
    def __init__(self, approver_id: str = "cli-human") -> None:
        self.id = approver_id

    def decide(self, surface: ApprovalSurface) -> ApprovalDecision:
        print(surface.render())
        answer = input("approve? [y/N] ").strip().lower()
        approved = answer in {"y", "yes"}
        return ApprovalDecision(approved, self.id, f"cli answer: {answer or 'n'}")
