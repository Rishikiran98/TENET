"""Tests for the executor + sandboxed fs tools (build step 6).

The load-bearing assertions are the two §8 boundaries: the path jail (nothing
can name a file outside the sandbox — including via symlinks) and the
constraint re-check at the executor (defense in depth: enforced even when the
executor is called directly, as if something bypassed the gate).
"""

import pytest

from tenet import HashingEmbedder, MemoryCore
from tenet.agent import AgentLoop, Proposal, ScriptedBrain, ToolExecutionError
from tenet.approver import FailSafeDenyApprover
from tenet.events import taxonomy as T
from tenet.executor import SandboxViolation, fs_executor
from tenet.gate import DefaultGate, GateDecision, Verdict
from tenet.scope import ScopeGrant, ToolGrant


def _grant(*tool_grants):
    return ScopeGrant.issue(
        principal="alice", task_id="task-1", namespaces={"proj"}, tools=tool_grants,
    )


def _tg(tool, **constraints):
    return ToolGrant(tool=tool, constraints=constraints)


# -- round-trip --------------------------------------------------------------

def test_write_read_delete_round_trip(tmp_path):
    ex = fs_executor(tmp_path / "sandbox")
    write = ex.execute(
        Proposal(tool="fs.write", args={"path": "notes/a.txt", "content": "hello"}),
        _tg("fs.write"),
    )
    assert write["bytes_written"] == 5
    read = ex.execute(
        Proposal(tool="fs.read", args={"path": "/notes/a.txt"}),  # leading / = root
        _tg("fs.read"),
    )
    assert read["content"] == "hello"
    delete = ex.execute(
        Proposal(tool="fs.delete", args={"path": "notes/a.txt"}), _tg("fs.delete"))
    assert delete["deleted"] is True
    assert not (tmp_path / "sandbox" / "notes" / "a.txt").exists()


# -- the jail ------------------------------------------------------------------

def test_dotdot_traversal_is_jailed(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    ex = fs_executor(tmp_path / "sandbox")
    with pytest.raises(SandboxViolation):
        ex.execute(Proposal(tool="fs.read", args={"path": "../outside.txt"}),
                   _tg("fs.read"))
    with pytest.raises(SandboxViolation):
        ex.execute(Proposal(tool="fs.delete", args={"path": "a/../../outside.txt"}),
                   _tg("fs.delete"))
    assert outside.exists()  # nothing outside was touched


def test_symlink_escape_is_jailed(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    root = tmp_path / "sandbox"
    ex = fs_executor(root)
    (root / "sneaky").symlink_to(outside)  # innocent-looking in-sandbox path

    with pytest.raises(SandboxViolation):
        ex.execute(Proposal(tool="fs.read", args={"path": "sneaky"}), _tg("fs.read"))
    assert outside.read_text() == "secret"


# -- constraint re-check (defense in depth) ------------------------------------

def test_path_prefix_recheck_at_executor(tmp_path):
    # Call the executor DIRECTLY — as if the gate had been bypassed. The
    # constraint must still hold.
    ex = fs_executor(tmp_path / "sandbox")
    with pytest.raises(SandboxViolation):
        ex.execute(
            Proposal(tool="fs.write", args={"path": "other/x.txt", "content": "x"}),
            _tg("fs.write", path_prefix="/notes"),
        )
    ok = ex.execute(
        Proposal(tool="fs.write", args={"path": "notes/x.txt", "content": "x"}),
        _tg("fs.write", path_prefix="/notes"),
    )
    assert ok["bytes_written"] == 1


def test_prefix_match_is_component_wise_not_string(tmp_path):
    ex = fs_executor(tmp_path / "sandbox")
    with pytest.raises(SandboxViolation):
        ex.execute(
            Proposal(tool="fs.write", args={"path": "notes2/x.txt", "content": "x"}),
            _tg("fs.write", path_prefix="/notes"),  # /notes must not cover /notes2
        )


# -- closed registry + failure taxonomy ----------------------------------------

def test_unknown_tool_is_rejected_by_the_closed_registry(tmp_path):
    ex = fs_executor(tmp_path / "sandbox")
    with pytest.raises(ToolExecutionError, match="closed registry"):
        ex.execute(Proposal(tool="net.fetch", args={"url": "http://x"}), _tg("net.fetch"))


def test_missing_file_is_plain_failure_not_violation(tmp_path):
    ex = fs_executor(tmp_path / "sandbox")
    with pytest.raises(ToolExecutionError) as err:
        ex.execute(Proposal(tool="fs.read", args={"path": "ghost.txt"}), _tg("fs.read"))
    assert not isinstance(err.value, SandboxViolation)


# -- end to end through the loop ------------------------------------------------

class AllowReads:
    version = "test/allow-reads-v1"

    def evaluate(self, proposal, grant, tool_grant):
        if proposal.tool == "fs.read":
            return GateDecision(Verdict.ALLOW, ("allow-read",), "reads are allowed")
        return None


def test_loop_drives_real_executor_and_events_outcomes(tmp_path):
    root = tmp_path / "sandbox"
    ex = fs_executor(root)
    SandboxedRootFile = root / "runbook.md"
    root.mkdir(parents=True, exist_ok=True)
    SandboxedRootFile.write_text("deploy on fridays")

    memory = MemoryCore(embedder=HashingEmbedder())
    loop = AgentLoop(memory=memory, gate=DefaultGate(), policy=AllowReads(),
                     approver=FailSafeDenyApprover(), executor=ex)
    brain = ScriptedBrain([
        Proposal(tool="fs.read", args={"path": "runbook.md"}),      # executes
        Proposal(tool="fs.read", args={"path": "../escape.txt"}),   # jailed → failed
    ])
    result = loop.run_task(
        task="read the runbook",
        grant=_grant(_tg("fs.read")), brain=brain, namespace="proj",
    )

    outcomes = [s.outcome_event_type for s in result.steps]
    assert outcomes == [T.ACTION_EXECUTED, T.ACTION_FAILED]
    executed = [e for e in memory.log if e.event_type == T.ACTION_EXECUTED]
    assert executed[0].payload["result"]["content"] == "deploy on fridays"
    failed = [e for e in memory.log if e.event_type == T.ACTION_FAILED]
    assert "jail" in failed[0].payload["error"]
    assert memory.log.verify() is True
