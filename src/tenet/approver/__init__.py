"""Tenet's approver layer (build step 5): the escalation decision point.

The approval surface renders the proposal and the gate's rationale — never
memory content. Implementations: scripted (tests/demo), fail-safe deny
(default when no human is reachable), and CLI (interactive).
"""

from __future__ import annotations

from .cli import CLIApprover
from .failsafe import FailSafeDenyApprover
from .protocol import ApprovalDecision, ApprovalSurface, Approver
from .scripted import ScriptedApprover

__all__ = [
    "Approver",
    "ApprovalSurface",
    "ApprovalDecision",
    "ScriptedApprover",
    "FailSafeDenyApprover",
    "CLIApprover",
]
