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


# --- follow-up review (items reported after the first fix round) -----------

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from tenet.contracts import ToolExecutionError
from tenet.events import Actor, InMemoryEventLog, SQLiteEventLog
from tenet.memory import ChunkEmbedContextualizer, IngestConflict, InMemoryContextStore
from tenet.projections import audit_view, render_audit
from tenet.recovery import reconcile_journal
from test_journal_recovery import setup as journal_setup

SYSTEM = Actor("system", "fixture")
DIGEST = "a" * 64


def malformed_journal_log():
    """A journal proposal missing 'content', dispatched and then 'reconciled'."""
    log = InMemoryEventLog()
    grant = {"grant_id": "g", "task_id": "t", "expires_at": "2999-01-01T00:00:00+00:00",
             "max_actions": 1, "namespaces": ["n"],
             "tools": [{"tool": "journal.append", "constraints": {"journal": "reports"}}]}
    init = log.append(namespace="n", actor=Actor("user", "u"), event_type=T.TASK_INITIATED,
                      payload={"description": "d", "grant": grant}, correlation_id="t")
    prop = log.append(namespace="n", actor=Actor("agent", "a"), event_type=T.AGENT_ACTION_PROPOSED,
                      payload={"tool": "journal.append", "args": {"journal": "reports"},
                               "justification": "", "cited_context_ids": [],
                               "proposal_digest": DIGEST},
                      correlation_id="t", causation_id=init.event_id)
    dispatch = log.append(namespace="n", actor=SYSTEM, event_type=T.ACTION_DISPATCHED,
                          payload={"operation_id": prop.event_id, "proposal_digest": DIGEST,
                                   "grant_id": "g", "executor_id": "store",
                                   "accepted_at": "2026-10-11T00:00:00+00:00"},
                          correlation_id="t", causation_id=prop.event_id)
    log.append(namespace="n", actor=SYSTEM, event_type=T.ACTION_RECONCILED,
               payload={"operation_id": prop.event_id, "proposal_digest": DIGEST, "grant_id": "g",
                        "executor_id": "store", "state": "applied", "evidence": {}},
               correlation_id="t", causation_id=dispatch.event_id)
    return log, prop.event_id


def test_malformed_journal_trace_is_reported_not_raised():
    log, op = malformed_journal_log()
    report = audit_trace(log)
    assert any("malformed action.reconciled" in issue for issue in report.issues)
    assert op in report.indeterminate_operations  # a bad resolution resolves nothing
    assert report.issues_by_task["t"]


def _unrelated_bad_event(log):
    log.append(namespace="n", actor=SYSTEM, event_type=T.ACTION_BLOCKED,
               payload={"proposal_ref": "nope", "verdict_ref": "nope"}, correlation_id="other-task")


def test_unrelated_bad_event_does_not_block_reconciliation(tmp_path):
    log, store, _, _, dispatch = journal_setup(tmp_path)
    _unrelated_bad_event(log)
    assert audit_trace(log).issues_by_task.get("other-task")
    event = reconcile_journal(log, store, dispatch.payload["operation_id"])
    assert event.payload["state"] == "cancelled"
    store.close(); log.close()


def test_bad_event_in_the_same_task_still_blocks_reconciliation(tmp_path):
    log, store, _, _, dispatch = journal_setup(tmp_path)
    log.append(namespace="n", actor=SYSTEM, event_type=T.ACTION_BLOCKED,
               payload={"proposal_ref": "nope", "verdict_ref": "nope"},
               correlation_id=dispatch.correlation_id)
    with pytest.raises(ValueError, match="invalid or legacy"):
        reconcile_journal(log, store, dispatch.payload["operation_id"])
    store.close(); log.close()


def test_audit_view_shows_reconciliation(tmp_path):
    log, store, _, _, dispatch = journal_setup(tmp_path)
    reconcile_journal(log, store, dispatch.payload["operation_id"])
    view = audit_view(log, dispatch.correlation_id)
    [trace] = view.proposals
    assert trace.outcome == T.ACTION_INDETERMINATE
    assert trace.reconciliation["state"] == "cancelled"
    assert "reconciled by operator: cancelled" in render_audit(view)
    store.close(); log.close()


def test_rebuild_does_not_hold_the_sqlite_write_lock(tmp_path):
    path = tmp_path / "events.db"
    memory = MemoryCore(HashingEmbedder(), log=SQLiteEventLog(path))
    memory.ingest("hello", namespace="n", source="s")
    inner = ChunkEmbedContextualizer(HashingEmbedder())

    class ProbeLock:
        version = inner.version
        acquired = False

        def contextualize(self, raw):
            other = sqlite3.connect(str(path), timeout=0.2, isolation_level=None)
            try:
                other.execute("BEGIN IMMEDIATE")  # what another writer's append needs
                other.execute("ROLLBACK")
                ProbeLock.acquired = True
            finally:
                other.close()
            return inner.contextualize(raw)

    memory._contextualizer = ProbeLock()
    memory.rebuild_projections()
    assert ProbeLock.acquired
    memory.log.close()


def test_journal_executor_without_store_identity_is_blocked():
    class NoIdentity:
        def execute(self, proposal, tool_grant):
            raise AssertionError("must not run")

        def execute_operation(self, proposal, tool_grant, dispatch):
            raise AssertionError("must not run")

    memory = MemoryCore(HashingEmbedder())
    loop = AgentLoop(memory=memory, gate=DefaultGate(), policy=AllowAll(),
                     approver=FailSafeDenyApprover(), executor=NoIdentity())
    grant = ScopeGrant.issue(principal="p", task_id="t", namespaces={"n"},
                             tools=(ToolGrant("journal.append", {"journal": "j"}),))
    result = loop.run_task(task="append", grant=grant, namespace="n",
                           brain=ScriptedBrain([Proposal("journal.append", {"journal": "j", "content": "c"})]))
    assert result.completed and result.steps[0].outcome_event_type == T.ACTION_BLOCKED
    assert audit_trace(memory.log).issues == ()


def test_ingest_conflict_has_its_own_type():
    memory = MemoryCore(HashingEmbedder())
    memory.ingest("a", namespace="n", source="s", ingest_id="k")
    with pytest.raises(IngestConflict):
        memory.ingest("b", namespace="n", source="s", ingest_id="k")
    assert issubclass(IngestConflict, ValueError)  # existing callers keep working


def test_construction_probes_emptiness_without_listing():
    class NoList(InMemoryContextStore):
        def list(self, namespace=None):
            raise AssertionError("list() must not be used to test emptiness")

    MemoryCore(HashingEmbedder(), context_store=NoList())


# --- third review pass ---------------------------------------------------------

def _initiated(log, task, grant):
    return log.append(namespace="n", actor=Actor("user", "u"), event_type=T.TASK_INITIATED,
                      payload={"description": "d", "grant": grant}, correlation_id=task)


def test_malformed_task_record_does_not_block_new_tasks():
    memory = MemoryCore(HashingEmbedder())
    _initiated(memory.log, "legacy", {"tools": []})  # no grant_id
    loop = AgentLoop(memory=memory, gate=DefaultGate(), policy=AllowAll(),
                     approver=FailSafeDenyApprover(), executor=fs_executor(tempfile.mkdtemp()))
    grant = ScopeGrant.issue(principal="p", task_id="fresh", namespaces={"n"})
    assert loop.run_task(task="x", grant=grant, namespace="n", brain=ScriptedBrain([])).completed
    with pytest.raises(ValueError, match="already started"):
        loop.run_task(task="x", grant=grant, namespace="n", brain=ScriptedBrain([]))


@pytest.mark.parametrize("grant, evidence", [
    ({"grant_id": "g", "namespaces": None, "tools": 7}, []),
    ({"grant_id": "g", "namespaces": ["n"], "tools": []}, None),
    ({"grant_id": "g", "namespaces": ["n"], "tools": []}, ["not-a-mapping"]),
])
def test_audit_view_tolerates_malformed_events(grant, evidence):
    log = InMemoryEventLog()
    init = _initiated(log, "t", grant)
    log.append(namespace="n", actor=Actor("agent", "a"), event_type=T.AGENT_RETRIEVAL_PERFORMED,
               payload={"query": "q", "scope": ["n"], "context_ids": [], "evidence": evidence},
               correlation_id="t", causation_id=init.event_id)
    view = audit_view(log, "t")
    text = render_audit(view)
    assert "TASK t" in text
    if evidence is not None and evidence != []:
        assert view.unreadable and "unreadable" in text


# --- Codex P2 on #18: non-object execution results ---------------------------

@pytest.mark.parametrize("result", [["a", "b"], "text", 7, None])
def test_non_object_execution_result_is_flagged_not_fabricated(tmp_path, result):
    memory = MemoryCore(HashingEmbedder())
    loop = AgentLoop(memory=memory, gate=DefaultGate(), policy=AllowAll(),
                     approver=FailSafeDenyApprover(), executor=fs_executor(tmp_path))
    (tmp_path / "a.txt").write_text("x")
    grant = ScopeGrant.issue(principal="p", task_id="t", namespaces={"n"},
                             tools=(ToolGrant("fs.read"),))
    loop.run_task(task="read", grant=grant, namespace="n",
                  brain=ScriptedBrain([Proposal("fs.read", {"path": "a.txt"})]))
    events = list(memory.log)
    [dispatch] = [e for e in events if e.event_type == T.ACTION_DISPATCHED]
    executed = next(e for e in events if e.event_type == T.ACTION_EXECUTED)

    # Rebuild the same trace with the receipt's result replaced.
    log, ids = InMemoryEventLog(), {}
    from tenet.values import thaw

    def remap(value):
        if isinstance(value, dict):
            return {k: remap(v) for k, v in value.items()}
        if isinstance(value, list):
            return [remap(v) for v in value]
        return ids.get(value, value) if isinstance(value, str) else value

    for e in events:
        payload = remap(thaw(e.payload))
        if e.event_id == executed.event_id:
            payload["result"] = result
        new = log.append(namespace=e.namespace, actor=e.actor, event_type=e.event_type,
                         payload=payload, correlation_id=e.correlation_id,
                         causation_id=ids.get(e.causation_id) if e.causation_id else None)
        ids[e.event_id] = new.event_id
    op = ids[dispatch.payload["operation_id"]]

    report = audit_trace(log)
    assert any("result is not an object" in issue for issue in report.issues)
    assert op in report.indeterminate_operations  # an unreadable receipt proves nothing

    view = audit_view(log, "t")
    [trace] = view.proposals
    assert trace.outcome == T.ACTION_EXECUTED
    assert trace.outcome_detail == {}  # no fabricated {"error": None}
    assert view.unreadable


def test_valid_execution_result_is_kept_as_recorded(tmp_path):
    memory = MemoryCore(HashingEmbedder())
    loop = AgentLoop(memory=memory, gate=DefaultGate(), policy=AllowAll(),
                     approver=FailSafeDenyApprover(), executor=fs_executor(tmp_path))
    (tmp_path / "a.txt").write_text("x")
    grant = ScopeGrant.issue(principal="p", task_id="t", namespaces={"n"},
                             tools=(ToolGrant("fs.read"),))
    loop.run_task(task="read", grant=grant, namespace="n",
                  brain=ScriptedBrain([Proposal("fs.read", {"path": "a.txt"})]))
    [trace] = audit_view(memory.log, "t").proposals
    assert trace.outcome_detail["path"] == "a.txt" and "error" not in trace.outcome_detail
    assert audit_trace(memory.log).issues == ()
