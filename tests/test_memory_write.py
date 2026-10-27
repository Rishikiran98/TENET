"""memory.write (§7): an agent persists memory only as a gated action.

The grant must name the tool and the one namespace it may write, the gate must
allow the proposal, and the executor re-checks both. What it writes is ordinary
memory with agent provenance: data that later tasks retrieve as data."""

import pytest

from tenet import HashingEmbedder, MemoryCore
from tenet.agent import AgentLoop, Proposal, ScriptedBrain
from tenet.agent.llm_brain import ACTIONS, LLMBrain
from tenet.approver import FailSafeDenyApprover
from tenet.constraints import MEMORY_WRITE_LIMIT, ConstraintViolation, check_constraints
from tenet.contracts import ToolExecutionError
from tenet.demo.policy import DemoFixturePolicy
from tenet.events import new_ulid, taxonomy as T
from tenet.events.audit import audit_trace
from tenet.executor import AGENT_SOURCE, MemoryWrite, fs_executor
from tenet.gate import DefaultGate, GateDecision, Verdict
from tenet.projections import audit_view, render_audit
from tenet.scope import ScopeGrant, ToolGrant

NOTE = "The release checklist lives in /project/RELEASE.md."


class AllowInGrant:
    """Test fixture policy (not production policy, D8): allow what the grant allows."""
    version = "test-fixture/v1"

    def evaluate(self, proposal, grant, tool_grant):
        return GateDecision(Verdict.ALLOW, ("fixture",), "in-grant fixture")


def grant(*tools, max_actions=3, namespaces=("notes",)):
    return ScopeGrant.issue(principal="operator", task_id=new_ulid(), namespaces=set(namespaces),
                            tools=tools, max_actions=max_actions)


WRITE_NOTES = ToolGrant("memory.write", {"namespace": "notes"})


def run(memory, tmp_path, g, *proposals, policy=None, register=True):
    executor = fs_executor(tmp_path, memory=memory if register else None)
    loop = AgentLoop(memory=memory, gate=DefaultGate(), policy=policy or AllowInGrant(),
                     approver=FailSafeDenyApprover(), executor=executor)
    return loop.run_task(task="remember where the checklist is", grant=g, namespace="notes",
                         brain=ScriptedBrain(list(proposals)))


def write(content=NOTE, namespace="notes"):
    return Proposal("memory.write", {"namespace": namespace, "content": content}, "remember it")


def test_granted_write_is_executed_and_retrievable_with_agent_provenance(tmp_path):
    memory = MemoryCore(HashingEmbedder())
    g = grant(WRITE_NOTES)
    result = run(memory, tmp_path, g, write())
    [step] = result.steps
    assert step.outcome_event_type == T.ACTION_EXECUTED

    receipt = step.observation.result
    appended = [e for e in memory.log if e.event_type == T.MEMORY_RAW_APPENDED]
    [event] = appended
    assert event.event_id == receipt["raw_id"] and event.namespace == "notes"
    assert event.payload["content"] == NOTE
    assert event.payload["source"] == AGENT_SOURCE
    assert (event.actor.kind, event.actor.id) == ("tool", "memory.write")
    assert receipt["bytes_written"] == len(NOTE.encode()) and "content" not in receipt

    # Later tasks see it as ordinary memory, retrieved as data.
    later = memory.retrieve("release checklist", grant(namespaces=("notes",)))
    assert [r.record.source for r in later] == [AGENT_SOURCE]
    assert audit_trace(memory.log).issues == ()


@pytest.mark.parametrize("tools, proposal", [
    ((), write()),                                                    # tool not granted
    ((WRITE_NOTES,), write(namespace="ops")),                         # namespace outside grant
    ((ToolGrant("memory.write", {}),), write()),                      # grant names no namespace
    ((ToolGrant("memory.write", {"namespace": "notes", "x": 1}),), write()),  # unknown constraint
    ((WRITE_NOTES,), write(content="")),
    ((WRITE_NOTES,), write(content="x" * (MEMORY_WRITE_LIMIT + 1))),
    ((WRITE_NOTES,), Proposal("memory.write", {"namespace": "notes", "content": NOTE,
                                               "source": "user"})),  # no forged provenance
])
def test_writes_outside_the_grant_are_blocked_and_nothing_is_written(tmp_path, tools, proposal):
    memory = MemoryCore(HashingEmbedder())
    result = run(memory, tmp_path, grant(*tools), proposal)
    assert [s.outcome_event_type for s in result.steps] == [T.ACTION_BLOCKED]
    assert not [e for e in memory.log if e.event_type == T.MEMORY_RAW_APPENDED]
    assert audit_trace(memory.log).issues == ()


def test_unmatched_policy_denies_a_granted_write(tmp_path):
    """D7: no policy rule confers it, so the default gate denies. Production
    policy for memory.write is Sai's to write (D8)."""
    memory = MemoryCore(HashingEmbedder())
    result = run(memory, tmp_path, grant(WRITE_NOTES), write(), policy=DemoFixturePolicy())
    assert [s.outcome_event_type for s in result.steps] == [T.ACTION_BLOCKED]
    assert not [e for e in memory.log if e.event_type == T.MEMORY_RAW_APPENDED]


def test_unregistered_tool_fails_instead_of_writing(tmp_path):
    memory = MemoryCore(HashingEmbedder())
    result = run(memory, tmp_path, grant(WRITE_NOTES), write(), register=False)
    assert [s.outcome_event_type for s in result.steps] == [T.ACTION_FAILED]
    assert not [e for e in memory.log if e.event_type == T.MEMORY_RAW_APPENDED]


def test_write_consumes_budget(tmp_path):
    memory = MemoryCore(HashingEmbedder())
    result = run(memory, tmp_path, grant(WRITE_NOTES, max_actions=1), write(), write("second"))
    assert [s.outcome_event_type for s in result.steps] == [T.ACTION_EXECUTED, T.ACTION_BLOCKED]
    assert len([e for e in memory.log if e.event_type == T.MEMORY_RAW_APPENDED]) == 1


def test_replay_rebuilds_agent_written_memory(tmp_path):
    memory = MemoryCore(HashingEmbedder())
    run(memory, tmp_path, grant(WRITE_NOTES), write())
    replayed = MemoryCore(HashingEmbedder(), log=memory.log)
    found = replayed.retrieve("release checklist", grant(namespaces=("notes",)))
    assert [r.record.source for r in found] == [AGENT_SOURCE]


def test_executor_rechecks_constraints_itself():
    """Defense in depth (§8): a direct call that skipped the gate is refused."""
    memory = MemoryCore(HashingEmbedder())
    with pytest.raises(ToolExecutionError):
        MemoryWrite(memory).execute({"namespace": "ops", "content": NOTE}, {"namespace": "notes"})
    with pytest.raises(ToolExecutionError):
        MemoryWrite(memory).execute({"namespace": "notes", "content": NOTE}, {})
    assert len(memory.log) == 0


def test_schema_is_exact():
    check_constraints("memory.write", {"namespace": "n", "content": "c"}, {"namespace": "n"})
    for args in ({"namespace": "n"}, {"namespace": 1, "content": "c"},
                 {"namespace": "n", "content": "\ud800"}):
        with pytest.raises(ConstraintViolation):
            check_constraints("memory.write", args, {"namespace": "n"})


def test_audit_view_shows_the_write(tmp_path):
    memory = MemoryCore(HashingEmbedder())
    g = grant(WRITE_NOTES)
    run(memory, tmp_path, g, write())
    view = audit_view(memory.log, g.task_id)
    [trace] = view.proposals
    assert trace.tool == "memory.write" and trace.outcome == T.ACTION_EXECUTED
    assert "memory.write" in render_audit(view)


def test_llm_brain_can_propose_a_write():
    assert ACTIONS["memory_write"] == ("memory.write", ("namespace", "content"))
    response = {"choices": [{"message": {"tool_calls": [{"function": {
        "name": "memory_write",
        "arguments": '{"namespace": "notes", "content": "x", "justification": "j",'
                     ' "cited_context_ids": []}'}}]}}]}
    proposal = LLMBrain(transport=lambda u, b, h: response).propose("t", [])
    assert proposal.tool == "memory.write" and dict(proposal.args) == {"namespace": "notes",
                                                                      "content": "x"}


def test_http_task_reports_the_write_receipt_without_content(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from tenet.api import create_app
    from test_api import AUTH, TOKEN, run_task

    memory = MemoryCore(HashingEmbedder())
    app = create_app(memory=memory, executor=fs_executor(tmp_path, memory=memory),
                     policy=AllowInGrant(), brain_factory=lambda: ScriptedBrain([write()]),
                     token=TOKEN)
    with TestClient(app, headers=AUTH) as client:
        body = run_task(client, {"task": "remember", "namespace": "notes", "grant": {
            "namespaces": ["notes"], "max_actions": 1, "ttl_seconds": 60,
            "tools": [{"tool": "memory.write", "constraints": {"namespace": "notes"}}]}})
    [step] = body["steps"]
    assert step["outcome"] == T.ACTION_EXECUTED
    assert set(step["result"]) == {"namespace", "raw_id", "content_hash", "bytes_written"}
