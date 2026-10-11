"""A3 recovery comparison: TENET journal vs. an independent ordinary-table controller.

Both systems run the same bounded task under the same crash schedule:

    1. read /notes/work.txt                         (allowed)
    2. write /protected/target.txt                  (outside the grant: must be refused)
    3. append the normalized note to journal "reports"

Each case runs the task in a child process that dies with ``os._exit(23)`` at
one checkpoint. For the two recovery checkpoints, a first child dies after the
dispatch commit and a second child dies inside or after reconciliation. The
parent then reconciles every unresolved operation, tries to reuse the task, and
checks an external oracle that reads the note, the protected file and the
journal database directly; neither controller's own verdict is the oracle.

This models process death, not power loss. Timings are single runs, recorded
only to show the order of magnitude, not as a benchmark.

Run from the repository root:  PYTHONPATH=src python evaluation/recovery_compare.py
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

from recovery_conventional import ConventionalRecovery

T0 = 2_000_000_000.0
NOTE = "  Work note:\n  Ship the A3   recovery comparison.  \n"
PROTECTED = "do not overwrite\n"
REPORT = "report: " + " ".join(NOTE.split()).lower()
TASK, GRANT_ID, JOURNAL = "recovery-task", "recovery-grant", "reports"
RUN_PHASES = ("none", "before_dispatch_commit", "after_dispatch_commit", "after_effect")
RECOVERY_PHASES = ("during_reconciliation", "after_reconciliation")


def crash_at(target):
    def checkpoint(phase):
        if phase == target:
            os._exit(23)
    return checkpoint


def fixture(root):
    (root / "notes").mkdir(parents=True, exist_ok=True)
    (root / "protected").mkdir(parents=True, exist_ok=True)
    (root / "notes" / "work.txt").write_text(NOTE, encoding="utf-8")
    (root / "protected" / "target.txt").write_text(PROTECTED, encoding="utf-8")


def next_proposal(observations):
    """The deterministic Brain shared by both systems (as plain dicts)."""
    step = len(observations)
    if step == 0:
        return {"tool": "fs.read", "args": {"path": "/notes/work.txt"}}
    if step == 1:
        return {"tool": "fs.write", "args": {"path": "/protected/target.txt", "content": "overwritten"}}
    if step == 2:
        note = observations[0]
        content = note["content"] if isinstance(note, dict) else note
        return {"tool": "journal.append",
                "args": {"journal": JOURNAL, "content": "report: " + " ".join(content.split()).lower()}}
    return None


TOOLS = [{"tool": "fs.read", "constraints": {"path_prefix": "/notes"}},
         {"tool": "fs.write", "constraints": {"path_prefix": "/notes"}},
         {"tool": "journal.append", "constraints": {"journal": JOURNAL}}]


# --- TENET ------------------------------------------------------------------

def tenet_parts(root, checkpoint):
    from tenet import HashingEmbedder, MemoryCore
    from tenet.agent import AgentLoop, Proposal
    from tenet.approver import ScriptedApprover
    from tenet.events import SQLiteEventLog, taxonomy as T
    from tenet.executor import fs_executor
    from tenet.gate import DefaultGate, GateDecision, Verdict
    from tenet.journal import JournalExecutor
    from tenet.scope import ScopeGrant, ToolGrant

    class CheckpointLog(SQLiteEventLog):
        def append_committed(self, **kwargs):
            journal = kwargs.get("event_type") == T.ACTION_DISPATCHED and "executor_id" in kwargs["payload"]
            if journal:
                checkpoint("before_dispatch_commit")
            event = super().append_committed(**kwargs)
            if journal:
                checkpoint("after_dispatch_commit")
            return event

    store = JournalExecutor(root / "journal.db", fallback=fs_executor(root))

    class Executor:
        store_id = store.store_id

        def execute(self, proposal, tool_grant):
            return store.execute(proposal, tool_grant)

        def execute_operation(self, proposal, tool_grant, dispatch):
            result = store.execute_operation(proposal, tool_grant, dispatch)
            checkpoint("after_effect")
            return result

    class AllowInGrant:
        version = "evaluation-fixture/v1"  # not production policy (D8)

        def evaluate(self, proposal, grant, tool_grant):
            return GateDecision(Verdict.ALLOW, ("fixture",), "in-grant fixture")

    class Brain:
        id = "recovery-fixture"

        def propose(self, task, context, *, observations=()):
            seen = [o.result["content"] if o.result and "content" in o.result else None
                    for o in observations]
            proposal = next_proposal(seen)
            return None if proposal is None else Proposal(proposal["tool"], proposal["args"])

    clock = lambda: datetime.fromtimestamp(T0, timezone.utc)
    log = CheckpointLog(root / "events.db")
    memory = MemoryCore(HashingEmbedder(), log=log, clock=clock)
    grant = ScopeGrant(GRANT_ID, "fixture", TASK, frozenset({"n"}),
                       tuple(ToolGrant(t["tool"], t["constraints"]) for t in TOOLS), 3,
                       datetime.fromtimestamp(T0 + 3600, timezone.utc).isoformat())
    loop = AgentLoop(memory=memory, gate=DefaultGate(), policy=AllowInGrant(),
                     approver=ScriptedApprover([]), executor=Executor(), clock=clock)
    return log, store, loop, grant, Brain


def tenet_run(root, checkpoint):
    log, store, loop, grant, Brain = tenet_parts(root, checkpoint)
    loop.run_task(task="recovery fixture", grant=grant, namespace="n", brain=Brain())
    log.close(); store.close()


def tenet_reconcile(root, checkpoint):
    from tenet.events.audit import audit_trace
    from tenet.recovery import reconcile_journal
    log, store, *_ = tenet_parts(root, lambda phase: None)
    real = store.reconcile

    def reconcile(*args):
        result = real(*args)
        checkpoint("during_reconciliation")  # store settled, log not yet
        return result
    store.reconcile = reconcile
    count = 0
    for op in audit_trace(log).indeterminate_operations:
        reconcile_journal(log, store, op)
        count += 1
        checkpoint("after_reconciliation")
    log.close(); store.close()
    return count


def tenet_recover(root):
    from tenet.events.audit import audit_trace
    interventions = tenet_reconcile(root, lambda phase: None)
    log, store, loop, grant, Brain = tenet_parts(root, lambda phase: None)
    audit = audit_trace(log)
    try:
        loop.run_task(task="retry", grant=grant, namespace="n", brain=Brain())
        rejected = False
    except ValueError as exc:
        rejected = "already started" in str(exc)
    log.close(); store.close()
    return dict(interventions=interventions, unresolved=len(audit.indeterminate_operations),
                audit_errors=len(audit.issues), task_reuse_rejected=rejected)


def tenet_journal_rows(root):
    with sqlite3.connect(root / "journal.db") as db:
        return db.execute("SELECT journal, content FROM journal_operations WHERE state='applied'").fetchall()


# --- conventional -------------------------------------------------------------

def conventional_grant():
    return {"task_id": TASK, "grant_id": GRANT_ID, "tools": TOOLS, "expiry": T0 + 3600, "budget": 3}


def conventional_brain(observations):
    seen = [o["result"]["content"] if o.get("result") and "content" in o["result"] else None
            for o in observations]
    return next_proposal(seen)


def conventional_run(root, checkpoint):
    app = ConventionalRecovery(root, clock=lambda: T0, checkpoint=checkpoint)
    app.run(conventional_grant(), conventional_brain)
    app.close()


def conventional_reconcile(root, checkpoint):
    app = ConventionalRecovery(root, clock=lambda: T0, checkpoint=checkpoint)
    count = 0
    for op in app.pending():
        app.reconcile(op)
        count += 1
    app.close()
    return count


def conventional_recover(root):
    interventions = conventional_reconcile(root, lambda phase: None)
    app = ConventionalRecovery(root, clock=lambda: T0)
    unresolved = len(app.pending())
    try:
        app.run(conventional_grant(), conventional_brain)
        rejected = False
    except sqlite3.IntegrityError:
        rejected = True
    app.close()
    return dict(interventions=interventions, unresolved=unresolved, audit_errors=None,
                task_reuse_rejected=rejected)


def conventional_journal_rows(root):
    with sqlite3.connect(root / "reports.db") as db:
        return db.execute("SELECT journal, content FROM reports WHERE state='applied'").fetchall()


SYSTEMS = {
    "tenet": (tenet_run, tenet_reconcile, tenet_recover, tenet_journal_rows),
    "conventional": (conventional_run, conventional_reconcile, conventional_recover,
                     conventional_journal_rows),
}


# --- harness --------------------------------------------------------------------

def child(system, stage, phase, root):
    run, reconcile, *_ = SYSTEMS[system]
    (run if stage == "run" else reconcile)(Path(root), crash_at(phase))


def spawn(system, stage, phase, root):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        [str(Path(__file__).resolve().parents[1] / "src"), os.environ.get("PYTHONPATH", "")])}
    process = subprocess.run([sys.executable, __file__, "--child", system, stage, phase, str(root)],
                             capture_output=True, text=True, timeout=60, env=env)
    expected = 0 if phase == "none" else 23
    if process.returncode != expected:
        raise RuntimeError(f"{system}/{stage}/{phase} exited {process.returncode}: {process.stderr}")
    return process.returncode


EXPECT_COMPLETED = {"none": True, "before_dispatch_commit": False, "after_dispatch_commit": False,
                    "after_effect": True, "during_reconciliation": False,
                    "after_reconciliation": False}


def case(system, phase):
    *_, recover, journal_rows = SYSTEMS[system]
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        fixture(root)
        started = time.perf_counter()
        child_interventions = 0
        if phase in RECOVERY_PHASES:
            spawn(system, "run", "after_dispatch_commit", root)
            spawn(system, "reconcile", phase, root)
            child_interventions = 1
        else:
            spawn(system, "run", phase, root)
        outcome = recover(root)
        elapsed = time.perf_counter() - started
        rows = journal_rows(root)
        note_ok = (root / "notes" / "work.txt").read_text(encoding="utf-8") == NOTE
        protected_ok = (root / "protected" / "target.txt").read_text(encoding="utf-8") == PROTECTED
        completed = rows == [(JOURNAL, REPORT)]
        violations = int(not note_ok) + int(not protected_ok) + sum(
            1 for journal, content in rows if (journal, content) != (JOURNAL, REPORT))
        duplicates = max(0, len(rows) - 1)
        interventions = outcome["interventions"] + child_interventions
        passed = (violations == 0 and duplicates == 0 and outcome["unresolved"] == 0 and
                  outcome["task_reuse_rejected"] and outcome["audit_errors"] in (0, None) and
                  completed == EXPECT_COMPLETED[phase])
        return dict(system=system, crash=phase, completed=completed, violations=violations,
                    duplicates=duplicates, unresolved=outcome["unresolved"],
                    interventions=interventions, audit_errors=outcome["audit_errors"],
                    task_reuse_rejected=outcome["task_reuse_rejected"],
                    seconds=round(elapsed, 3), passed=passed)


def main():
    results = [case(system, phase) for system in SYSTEMS
               for phase in RUN_PHASES + RECOVERY_PHASES]
    summary = {system: {"passed": sum(r["passed"] for r in results if r["system"] == system),
                        "total": sum(r["system"] == system for r in results)}
               for system in SYSTEMS}
    print(json.dumps({"cases": results, "summary": summary}, indent=2))
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", nargs=4, metavar=("SYSTEM", "STAGE", "PHASE", "ROOT"))
    args = parser.parse_args()
    if args.child:
        child(*args.child)
    else:
        sys.exit(main())
