"""The governance gate — contract and pure-function mechanism (§6, D7).

The gate is a **pure function**: no IO, no LLM, no retrieval. It judges; it does
not investigate. That purity is what makes it testable, auditable, and
defensible line by line — and it is why emitting ``gate.verdict.issued`` is the
*loop's* job, not the gate's.

Division of labor (D8): this module is the **contract + mechanism** — the
evaluation order, the grant checks, and default-deny. The **policy content** —
the actual rules — is authored separately in ``gate/policy.py`` (Sai,
describe-first). The gate consumes a ``Policy``; it does not embed rules.

Evaluation order (D7), fixed:

    1. Grant authority first. Expiry, action budget, and tool membership are
       grant checks — any failure is DENY *before* policy is consulted.
    2. Policy rules. For an in-grant proposal the policy returns a decision, or
       ``None`` meaning "no rule matched."
    3. Unmatched ⇒ DENY. Escalation is a privilege a rule explicitly confers on
       a recognized grey zone; it is never a fallback for policy gaps.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable

from ..agent.proposal import Proposal
from ..scope import ScopeGrant, ToolGrant


class Verdict(str, Enum):
    """str-valued so the verdict serializes straight into the
    ``gate.verdict.issued`` payload as "allow"/"deny"/"escalate"."""

    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class GateDecision:
    verdict: Verdict
    matched_rule_ids: tuple[str, ...]
    rationale: str                  # human-readable; lands in the audit trail

    @property
    def allowed(self) -> bool:
        return self.verdict is Verdict.ALLOW


@runtime_checkable
class Policy(Protocol):
    """The rule layer — authored by Sai (D8), not here. Given an *in-grant*
    proposal and the resolved ToolGrant, it returns a decision, or ``None`` for
    "no rule matched" (which the gate turns into DENY). ``version`` is recorded
    on every verdict event as ``policy_version``."""

    version: str

    def evaluate(
        self, proposal: Proposal, grant: ScopeGrant, tool_grant: ToolGrant
    ) -> GateDecision | None: ...


@runtime_checkable
class Gate(Protocol):
    def evaluate(
        self,
        proposal: Proposal,
        grant: ScopeGrant,
        policy: Policy,
        *,
        now: datetime | None = None,
        actions_used: int = 0,
    ) -> GateDecision: ...


class DefaultGate:
    """The gate mechanism. Pure: identical inputs always yield the identical
    decision, and nothing here performs a side effect."""

    def evaluate(
        self,
        proposal: Proposal,
        grant: ScopeGrant,
        policy: Policy,
        *,
        now: datetime | None = None,
        actions_used: int = 0,
    ) -> GateDecision:
        # 1. grant authority — default-deny, before any policy rule is consulted
        if grant.is_expired(now):
            return GateDecision(Verdict.DENY, (), "grant expired")
        if actions_used >= grant.max_actions:
            return GateDecision(
                Verdict.DENY, (), f"action budget exhausted (max_actions={grant.max_actions})"
            )
        tool_grant = grant.tool_grant(proposal.tool)
        if tool_grant is None:
            return GateDecision(
                Verdict.DENY, (), f"tool {proposal.tool!r} not in grant (default-deny)"
            )

        # 2. policy rules for an in-grant proposal
        decision = policy.evaluate(proposal, grant, tool_grant)

        # 3. unmatched ⇒ DENY — never a silent escalate
        if decision is None:
            return GateDecision(Verdict.DENY, (), "no policy rule matched (default-deny)")
        return decision
