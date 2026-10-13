"""Escalations answered over HTTP: bound, fail-closed, and fully audited."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from tenet import HashingEmbedder, MemoryCore
from tenet.agent import Proposal, ScriptedBrain
from tenet.api import create_app
from tenet.events import Actor, taxonomy as T
from tenet.events.audit import audit_trace
from tenet.executor import fs_executor
from tenet.gate import GateDecision, Verdict
from tenet.projections import audit_view, render_audit
from test_api import AUTH, TOKEN, run_task, wait_for

GRANT = {"namespaces": ["n"], "max_actions": 2, "ttl_seconds": 600,
         "tools": [{"tool": "fs.delete", "constraints": {"path_prefix": "/scratch"}}]}
TASK = {"task": "clean up the scratch file", "namespace": "n", "grant": GRANT}


class EscalateDeletes:
    """Test fixture, not production policy (D8): deletes need a human."""
    version = "test/escalate-deletes"

    def evaluate(self, proposal, grant, tool_grant):
        if proposal.tool == "fs.delete":
            return GateDecision(Verdict.ESCALATE, ("escalate-delete",), "deletes need approval")
        return None


def make(tmp_path, **kwargs):
    (tmp_path / "scratch").mkdir()
    target = tmp_path / "scratch" / "old.txt"
    target.write_text("stale")
    memory = MemoryCore(HashingEmbedder())
    brain = lambda: ScriptedBrain([Proposal("fs.delete", {"path": "/scratch/old.txt"})])
    app = create_app(memory=memory, executor=fs_executor(tmp_path), policy=EscalateDeletes(),
                     brain_factory=brain, token=TOKEN, **kwargs)
    return TestClient(app), memory, target


def pending(client):
    task_id = client.post("/tasks", headers=AUTH, json=TASK).json()["task_id"]
    state = wait_for(client, task_id, until=("awaiting_approval",))
    return task_id, state["pending_approval"]


def answer(client, task_id, approval, decision, binding=None):
    return client.post(f"/tasks/{task_id}/approvals/{approval['proposal_ref']}", headers=AUTH,
                       json={"decision": decision, "binding": binding or approval["binding"],
                             "note": f"{decision} via test"})


def test_approved_escalation_executes_and_is_audited(tmp_path):
    client, memory, target = make(tmp_path)
    task_id, approval = pending(client)
    assert approval["tool"] == "fs.delete" and approval["args"] == {"path": "/scratch/old.txt"}
    assert "(untrusted)" in approval["surface"] and approval["binding"] in approval["surface"]
    assert target.exists()  # nothing runs before the decision

    assert answer(client, task_id, approval, "approve").status_code == 200
    state = wait_for(client, task_id)
    assert state["status"] == "completed"
    assert [(s["verdict"], s["outcome"]) for s in state["steps"]] == [("escalate", T.ACTION_EXECUTED)]
    assert not target.exists()

    assert audit_trace(memory.log).issues == ()
    [decided] = [e for e in memory.log if e.event_type == T.APPROVAL_DECIDED]
    assert decided.payload["decision"] == "approve"
    assert decided.payload["approver_id"] == "operator/http"
    assert "approver operator/http: approve" in render_audit(audit_view(memory.log, task_id))


def test_rejected_escalation_is_blocked(tmp_path):
    client, memory, target = make(tmp_path)
    task_id, approval = pending(client)
    assert answer(client, task_id, approval, "reject").status_code == 200
    state = wait_for(client, task_id)
    assert state["steps"][0]["outcome"] == T.ACTION_BLOCKED
    assert target.exists()
    assert audit_trace(memory.log).issues == ()


def test_wrong_binding_is_refused_and_stays_pending(tmp_path):
    client, memory, target = make(tmp_path)
    task_id, approval = pending(client)
    response = answer(client, task_id, approval, "approve", binding="f" * 64)
    assert response.status_code == 409
    state = client.get(f"/tasks/{task_id}", headers=AUTH).json()
    assert state["status"] == "awaiting_approval" and target.exists()
    assert answer(client, task_id, approval, "approve").status_code == 200
    assert wait_for(client, task_id)["status"] == "completed"
    assert not target.exists()


def test_unknown_task_or_proposal_is_404(tmp_path):
    client, _, _ = make(tmp_path)
    task_id, approval = pending(client)
    body = {"decision": "approve", "binding": approval["binding"]}
    assert client.post(f"/tasks/{task_id}/approvals/nope", headers=AUTH, json=body).status_code == 404
    assert client.post(f"/tasks/nope/approvals/{approval['proposal_ref']}", headers=AUTH,
                       json=body).status_code == 404
    answer(client, task_id, approval, "reject")
    wait_for(client, task_id)
    # Already answered: a second answer has nothing to land on.
    assert answer(client, task_id, approval, "approve").status_code == 404


def test_unanswered_escalation_times_out_as_deny(tmp_path):
    client, memory, target = make(tmp_path, approval_timeout=0.2)
    state = run_task(client, TASK)
    assert state["steps"][0]["outcome"] == T.ACTION_BLOCKED
    assert target.exists()
    [decided] = [e for e in memory.log if e.event_type == T.APPROVAL_DECIDED]
    assert decided.payload["decision"] == "reject" and "timed out" in decided.payload["note"]
    assert audit_trace(memory.log).issues == ()


def test_shutdown_denies_pending_escalations(tmp_path):
    client, memory, target = make(tmp_path)
    with client:  # enter/exit runs the app lifespan
        task_id, _ = pending(client)
    assert target.exists()
    [decided] = [e for e in memory.log if e.event_type == T.APPROVAL_DECIDED]
    assert decided.payload["decision"] == "reject" and "shutting down" in decided.payload["note"]
    assert any(e.event_type == T.ACTION_BLOCKED for e in memory.log)


def test_approvals_require_the_token(tmp_path):
    client, _, target = make(tmp_path)
    task_id, approval = pending(client)
    response = client.post(f"/tasks/{task_id}/approvals/{approval['proposal_ref']}",
                           json={"decision": "approve", "binding": approval["binding"]})
    assert response.status_code == 401 and target.exists()
    answer(client, task_id, approval, "reject")


def test_task_status_falls_back_to_the_log(tmp_path):
    client, memory, _ = make(tmp_path)
    grant = {"grant_id": "g", "task_id": "old", "expires_at": "2999-01-01T00:00:00+00:00",
             "max_actions": 0, "namespaces": ["n"], "tools": []}
    memory.log.append(namespace="n", actor=Actor("user", "u"), event_type=T.TASK_INITIATED,
                      payload={"description": "d", "grant": grant}, correlation_id="old")
    assert client.get("/tasks/old", headers=AUTH).json()["status"] == "interrupted"
    assert client.get("/tasks/never", headers=AUTH).status_code == 404


def test_explicit_approver_opts_out_of_http_approvals(tmp_path):
    from tenet.approver import FailSafeDenyApprover
    client, memory, target = make(tmp_path, approver=FailSafeDenyApprover())
    state = run_task(client, TASK)
    assert state["steps"][0]["outcome"] == T.ACTION_BLOCKED and target.exists()


def _read_app(tmp_path, **kwargs):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.txt").write_text("TOP-SECRET-READ-TEXT")
    memory = MemoryCore(HashingEmbedder())

    class AllowReads:
        version = "test/allow-reads"

        def evaluate(self, proposal, grant, tool_grant):
            return GateDecision(Verdict.ALLOW, ("allow-read",), "reads allowed")

    app = create_app(memory=memory, executor=fs_executor(tmp_path), policy=AllowReads(),
                     brain_factory=lambda: ScriptedBrain([Proposal("fs.read", {"path": "/notes/a.txt"})]),
                     token=TOKEN, **kwargs)
    task = {"task": "read", "namespace": "n",
            "grant": {"namespaces": ["n"], "max_actions": 1, "ttl_seconds": 600,
                      "tools": [{"tool": "fs.read", "constraints": {"path_prefix": "/notes"}}]}}
    return app, TestClient(app), task


def test_finished_tasks_keep_no_read_content(tmp_path):
    app, client, task = _read_app(tmp_path)
    state = run_task(client, task)
    assert state["steps"][0]["outcome"] == T.ACTION_EXECUTED
    record = app.state.tasks.get(state["task_id"])
    assert not hasattr(record, "result")
    assert "TOP-SECRET-READ-TEXT" not in repr(record)


def test_old_finished_tasks_are_evicted_and_served_from_the_log(tmp_path):
    app, client, task = _read_app(tmp_path, max_finished_tasks=1)
    first, second = run_task(client, task), run_task(client, task)
    assert app.state.tasks.get(first["task_id"]) is None
    assert app.state.tasks.get(second["task_id"]) is not None
    evicted = client.get(f"/tasks/{first['task_id']}", headers=AUTH).json()
    assert evicted["status"] == "completed" and evicted["steps"] == []
