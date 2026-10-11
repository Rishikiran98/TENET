"""DEMO FIXTURE POLICY — not production policy.

Production policy content is Sai's to author (D8). This fixture exists only so
the headline demo can run: in-grant reads are allowed, and nothing else is
matched, so the gate turns everything else into DENY. The demo's headline
denial happens at the grant check, before this policy is consulted.
"""

from __future__ import annotations

from ..contracts import Proposal
from ..gate import GateDecision, Verdict
from ..scope import ScopeGrant, ToolGrant


class DemoFixturePolicy:
    version = "demo-fixture/v1"

    def evaluate(
        self, proposal: Proposal, grant: ScopeGrant, tool_grant: ToolGrant
    ) -> GateDecision | None:
        if proposal.tool == "fs.read":
            return GateDecision(Verdict.ALLOW, ("demo-allow-read",), "in-grant reads are allowed")
        return None
