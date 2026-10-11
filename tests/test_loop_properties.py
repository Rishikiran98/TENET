"""Randomized loop runs: every log the loop writes must satisfy the audit.

Each seed draws a grant (tool subset, budget, expiry), a per-tool policy
(ALLOW / ESCALATE / unmatched), an approver (approve / reject / bad binding),
an executor that sometimes fails or becomes indeterminate, a clock that
advances, and a script of in- and out-of-grant proposals. Seeds are fixed, so
a failure reproduces exactly.
"""

import random
from datetime import datetime, timedelta, timezone

import pytest

from tenet import HashingEmbedder, MemoryCore
from tenet.agent import AgentLoop, Proposal
from tenet.approver.protocol import ApprovalDecision
from tenet.contracts import ToolExecutionError
from tenet.events import taxonomy as T
from tenet.events.audit import audit_trace
from tenet.executor import fs_executor
from tenet.gate import DefaultGate, GateDecision, Verdict
from tenet.journal import JournalExecutor
from tenet.projections import audit_view
from tenet.scope import ScopeGrant, ToolGrant

T0 = datetime(2030, 1, 1, tzinfo=timezone.utc)
TOOLS = ("fs.read", "fs.write", "fs.delete", "journal.append", "net.get")
PATHS = ("/notes/a.txt", "/notes/b.txt", "/secret.txt", "../x", "/notes")


def run_seed(seed, root):
    rnd = random.Random(seed)
    (root / "notes").mkdir()
    (root / "notes" / "a.txt").write_text("alpha")
    (root / "secret.txt").write_text("secret")
    now = [T0]

    def clock():
        now[0] += timedelta(seconds=rnd.choice([0, 1, 30]))
        return now[0]

    memory = MemoryCore(HashingEmbedder(), clock=clock)
    memory.ingest("read notes a.txt", namespace="n", source="doc")
    store = JournalExecutor(root / "journal.db", fallback=fs_executor(root))
    tools = [ToolGrant("fs.read", {"path_prefix": "/notes"}),
             ToolGrant("fs.write", {"path_prefix": "/notes"}),
             ToolGrant("fs.delete"), ToolGrant("journal.append", {"journal": "r"})]
    rnd.shuffle(tools)
    grant = ScopeGrant(f"g{seed}", "p", f"t{seed}", frozenset({"n"}),
                       tuple(tools[:rnd.randint(0, 4)]), rnd.randint(0, 4),
                       (T0 + timedelta(seconds=rnd.choice([5, 60, 3600]))).isoformat())
    verdicts = {}

    class Policy:
        version = "property-fixture/v1"

        def evaluate(self, proposal, grant, tool_grant):
            verdict = verdicts.setdefault(
                proposal.tool, rnd.choice([Verdict.ALLOW, Verdict.ESCALATE, None]))
            return None if verdict is None else GateDecision(verdict, ("r",), "fixture")

    class Approver:
        id = "approver"

        def decide(self, surface):
            mode = rnd.choice(["approve", "reject", "bad-binding"])
            binding = {"approve": surface.binding, "bad-binding": "0" * 64}.get(mode, "")
            return ApprovalDecision(mode != "reject", self.id, mode, binding=binding)

    class Executor:
        store_id = store.store_id

        def execute(self, proposal, tool_grant):
            roll = rnd.random()
            if roll < .1:
                raise ToolExecutionError("fixture failure")
            if roll < .15:
                raise ToolExecutionError("fixture uncertainty", outcome_unknown=True)
            return store.execute(proposal, tool_grant)

        def execute_operation(self, proposal, tool_grant, dispatch):
            if rnd.random() < .15:
                raise ToolExecutionError("fixture uncertainty", outcome_unknown=True)
            return store.execute_operation(proposal, tool_grant, dispatch)

    def draw():
        tool = rnd.choice(TOOLS)
        args = {"path": rnd.choice(PATHS)}
        if tool == "fs.write":
            args["content"] = "c"
        if tool == "journal.append":
            args = {"journal": rnd.choice(["r", "x"]), "content": "c"}
        return tool, args, rnd.choice(["none", "bogus", "retrieved"])

    script = [draw() for _ in range(rnd.randint(0, 6))]

    class Brain:
        id = "property-brain"

        def __init__(self):
            self.step = 0

        def propose(self, task, context, *, observations=()):
            if self.step >= len(script):
                return None
            tool, args, cite = script[self.step]
            self.step += 1
            cited = {"none": (), "bogus": ("bogus",),
                     "retrieved": tuple(r.record.context_id for r in context[:1])}[cite]
            return Proposal(tool, args, "fixture", cited)

    loop = AgentLoop(memory=memory, gate=DefaultGate(), policy=Policy(),
                     approver=Approver(), executor=Executor(), clock=clock)
    result = loop.run_task(task="read notes", grant=grant, namespace="n", brain=Brain())
    store.close()
    return memory.log, grant, result


@pytest.mark.parametrize("seed", range(200))
def test_loop_logs_satisfy_the_audit(seed, tmp_path):
    log, grant, result = run_seed(seed, tmp_path)

    report = audit_trace(log)
    assert report.issues == ()
    assert audit_view(log, grant.task_id).unreadable == []
    dispatched = sum(e.event_type == T.ACTION_DISPATCHED for e in log)
    assert dispatched <= grant.max_actions
    if not any(t.tool == "fs.delete" for t in grant.tools):
        assert (tmp_path / "secret.txt").exists()
    uncertain = [s for s in result.steps if s.outcome_event_type == T.ACTION_INDETERMINATE]
    assert len(report.indeterminate_operations) == len(uncertain)
