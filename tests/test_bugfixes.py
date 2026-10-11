"""Regressions for bugs found in the October 11 codebase review."""

import tempfile

from tenet import HashingEmbedder, MemoryCore
from tenet.agent import AgentLoop, Proposal, ScriptedBrain
from tenet.approver import FailSafeDenyApprover
from tenet.events import taxonomy as T
from tenet.events.audit import audit_trace
from tenet.executor import fs_executor
from tenet.gate import DefaultGate, GateDecision, Verdict
from tenet.scope import ScopeGrant, ToolGrant


def test_generator_citations_and_rule_ids_are_preserved():
    proposal = Proposal("fs.read", {"path": "/a"}, "", (c for c in ["ctx-1"]))
    assert proposal.cited_context_ids == ("ctx-1",)
    decision = GateDecision(Verdict.ALLOW, (r for r in ["rule-1"]), "ok")
    assert decision.matched_rule_ids == ("rule-1",)


def test_stale_context_store_is_cleared_for_an_empty_log():
    old = MemoryCore(HashingEmbedder())
    old.ingest("secret plan alpha", namespace="n", source="old")
    fresh = MemoryCore(HashingEmbedder(), context_store=old._context)
    grant = ScopeGrant.issue(principal="p", task_id="t", namespaces={"n"})
    assert fresh.retrieve("secret plan", grant) == []


class AllowAll:
    version = "test/allow-all"

    def evaluate(self, proposal, grant, tool_grant):
        return GateDecision(Verdict.ALLOW, ("allow",), "allowed")


def test_journal_proposal_without_journal_executor_is_blocked_not_crashed():
    memory = MemoryCore(HashingEmbedder())
    loop = AgentLoop(memory=memory, gate=DefaultGate(), policy=AllowAll(),
                     approver=FailSafeDenyApprover(), executor=fs_executor(tempfile.mkdtemp()))
    grant = ScopeGrant.issue(principal="p", task_id="t", namespaces={"n"},
                             tools=(ToolGrant("journal.append", {"journal": "j"}),))
    brain = ScriptedBrain([Proposal("journal.append", {"journal": "j", "content": "c"})])

    result = loop.run_task(task="append", grant=grant, brain=brain, namespace="n")

    assert result.completed
    assert [s.outcome_event_type for s in result.steps] == [T.ACTION_BLOCKED]
    verdicts = [e.payload for e in memory.log if e.event_type == T.GATE_VERDICT_ISSUED]
    assert [(v["phase"], v["verdict"]) for v in verdicts] == [("initial", "allow"), ("dispatch", "deny")]
    assert "journal" in verdicts[-1]["rationale"]
    assert not any(e.event_type == T.ACTION_DISPATCHED for e in memory.log)
    assert audit_trace(memory.log).issues == ()
