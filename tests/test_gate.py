"""Tests for the governance gate contract + mechanism (build step 4).

These exercise the *mechanism* (evaluation order, default-deny, unmatched⇒DENY)
and the event wiring. The policy stubs below are test fixtures only — the real
policy is authored separately (D8), so nothing here ships as policy content.
"""

from datetime import datetime, timedelta, timezone

import pytest

from tenet.agent import Proposal
from tenet.events import Actor, InMemoryEventLog
from tenet.events.taxonomy import AGENT_ACTION_PROPOSED, GATE_VERDICT_ISSUED
from tenet.gate import DefaultGate, GateDecision, Verdict
from tenet.scope import ScopeGrant, ToolGrant


# -- test-only policy fixtures (NOT shipped policy content) -----------------

class AllowReadEscalateDelete:
    version = "test/allow-read-escalate-delete-v1"

    def evaluate(self, proposal, grant, tool_grant):
        if proposal.tool == "fs.read":
            return GateDecision(Verdict.ALLOW, ("allow-read",), "reads are allowed")
        if proposal.tool == "fs.delete":
            return GateDecision(Verdict.ESCALATE, ("escalate-delete",), "deletes need approval")
        return None  # no rule matched


class AllowEverything:
    version = "test/allow-everything-v1"

    def evaluate(self, proposal, grant, tool_grant):
        return GateDecision(Verdict.ALLOW, ("allow-all",), "permissive test policy")


def _grant(*tools, max_actions=10, ttl_seconds=3600):
    return ScopeGrant.issue(
        principal="alice", task_id="t", namespaces={"proj"},
        tools=tuple(ToolGrant(tool=t) for t in tools),
        max_actions=max_actions, ttl_seconds=ttl_seconds,
    )


GATE = DefaultGate()


def test_policy_allow_and_escalate_pass_through():
    grant = _grant("fs.read", "fs.delete")
    allow = GATE.evaluate(Proposal(tool="fs.read"), grant, AllowReadEscalateDelete())
    assert allow.verdict is Verdict.ALLOW and allow.matched_rule_ids == ("allow-read",)

    esc = GATE.evaluate(Proposal(tool="fs.delete"), grant, AllowReadEscalateDelete())
    assert esc.verdict is Verdict.ESCALATE and esc.matched_rule_ids == ("escalate-delete",)


def test_tool_not_in_grant_is_denied_before_policy_runs():
    # Policy would ALLOW anything; the grant check must deny first (default-deny).
    grant = _grant("fs.read")  # fs.write not granted
    d = GATE.evaluate(Proposal(tool="fs.write"), grant, AllowEverything())
    assert d.verdict is Verdict.DENY
    assert "not in grant" in d.rationale
    assert d.matched_rule_ids == ()  # no policy rule was consulted


def test_expired_grant_is_denied_regardless_of_policy():
    grant = _grant("fs.read")
    past = datetime.now(timezone.utc) + timedelta(hours=2)
    d = GATE.evaluate(Proposal(tool="fs.read"), grant, AllowEverything(), now=past)
    assert d.verdict is Verdict.DENY and "expired" in d.rationale


def test_action_budget_exhausted_is_denied():
    grant = _grant("fs.read", max_actions=2)
    d = GATE.evaluate(Proposal(tool="fs.read"), grant, AllowEverything(), actions_used=2)
    assert d.verdict is Verdict.DENY and "budget" in d.rationale


def test_unmatched_policy_denies_never_escalates():
    # fs.write is in the grant but no policy rule matches -> DENY, not ESCALATE.
    grant = _grant("fs.read", "fs.write")
    d = GATE.evaluate(Proposal(tool="fs.write"), grant, AllowReadEscalateDelete())
    assert d.verdict is Verdict.DENY
    assert "no policy rule matched" in d.rationale


def test_gate_is_pure_and_deterministic():
    grant = _grant("fs.read")
    p = Proposal(tool="fs.read", args={"path": "a.txt"})
    d1 = GATE.evaluate(p, grant, AllowEverything())
    d2 = GATE.evaluate(p, grant, AllowEverything())
    assert d1 == d2  # identical inputs -> identical decision


def test_verdict_wiring_emits_gate_event_with_causation():
    # The loop (later) does this; here we pin the wiring shape: a proposal event
    # causes a gate.verdict.issued event, both valid under the closed taxonomy.
    log = InMemoryEventLog()
    grant = _grant("fs.read", "fs.delete")
    proposal = Proposal(tool="fs.delete", args={"path": "secrets.txt"},
                        justification="cleanup", cited_context_ids=("c1",))

    proposed = log.append(
        namespace="proj", actor=Actor(kind="agent", id="brain"),
        event_type=AGENT_ACTION_PROPOSED,
        payload={"tool": proposal.tool, "args": proposal.args,
                 "justification": proposal.justification,
                 "cited_context_ids": list(proposal.cited_context_ids)},
        correlation_id="task-1",
    )
    decision = GATE.evaluate(proposal, grant, AllowReadEscalateDelete())
    verdict = log.append(
        namespace="proj", actor=Actor(kind="gate", id="gate"),
        event_type=GATE_VERDICT_ISSUED,
        payload={"verdict": decision.verdict.value,
                 "policy_version": AllowReadEscalateDelete.version,
                 "matched_rule_ids": list(decision.matched_rule_ids),
                 "rationale": decision.rationale},
        correlation_id="task-1", causation_id=proposed.event_id,
    )

    assert decision.verdict is Verdict.ESCALATE
    assert verdict.causation_id == proposed.event_id      # why-chain intact
    assert verdict.payload["verdict"] == "escalate"
    assert log.verify() is True
