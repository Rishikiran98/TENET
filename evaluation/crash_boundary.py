"""Real process exits on either side of the external-effect boundary.

Both implementations must retain uncertainty and reject blind task reuse.
No claim of exactly-once execution or automatic reconciliation is made.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from conventional import Conventional
from compare import grant, proposal


def tenet(root):
    from tenet import MemoryCore, HashingEmbedder
    from tenet.agent import AgentLoop
    from tenet.approver import ScriptedApprover
    from tenet.events import SQLiteEventLog
    from tenet.executor import fs_executor
    from tenet.gate import DefaultGate, GateDecision, Verdict
    from tenet.scope import ScopeGrant, ToolGrant
    log = SQLiteEventLog(root/'events.db')
    memory = MemoryCore(HashingEmbedder(), log=log,
                        clock=lambda: datetime.fromtimestamp(2000000000., timezone.utc))
    class Policy:
        version = 'fixture/v1'
        def evaluate(self, *args): return GateDecision(Verdict.ALLOW, ('fixture',), 'fixture')
    g = grant()
    scope = ScopeGrant(g['grant_id'], 'fixture', g['task_id'], frozenset({'n'}),
                       (ToolGrant('fs.write'),), g['budget'],
                       datetime.fromtimestamp(g['expiry'], timezone.utc).isoformat())
    return memory, scope, dict(memory=memory, gate=DefaultGate(), policy=Policy(),
                              approver=ScriptedApprover([]), executor=fs_executor(root))


def child(system, phase, root):
    def crash(): os._exit(23)
    def sync_effect():
        with (root/'effect').open('rb') as f: os.fsync(f.fileno())
    if system == 'conventional':
        app = Conventional(root, database=root/'state.db', clock=lambda: 2000000000.)
        def effect(p, g):
            if phase == 'before_effect': crash()
            result = app.execute(p, g); sync_effect(); crash()
            return result
        app.run(grant(), [proposal(path='effect')], effect=effect)
    else:
        from tenet.agent import AgentLoop, Proposal, ScriptedBrain
        memory, scope, kwargs = tenet(root); executor = kwargs['executor']
        class CrashExecutor:
            def execute(self, p, g):
                if phase == 'before_effect': crash()
                result = executor.execute(p, g); sync_effect(); crash()
                return result
        kwargs['executor'] = CrashExecutor()
        AgentLoop(**kwargs).run_task(task='fixture', grant=scope, namespace='n',
                                    brain=ScriptedBrain([Proposal('fs.write', {'path':'effect','content':'new'})]))
    raise AssertionError('child should have exited')


def check(system, phase):
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        process = subprocess.run([sys.executable, __file__, '--child', system, phase, folder],
                                 capture_output=True, text=True, timeout=20)
        if process.returncode != 23:
            raise RuntimeError(f'child failed unexpectedly: {process.stderr}')
        exists = (root/'effect').exists()
        rejected = False
        if system == 'conventional':
            app = Conventional(root, database=root/'state.db', clock=lambda: 2000000000.)
            unknown = len(app.indeterminate())
            try: app.run(grant(), [proposal(path='effect')])
            except Exception as exc:
                import sqlite3
                if not isinstance(exc, sqlite3.IntegrityError): raise
                rejected = True
            app.close()
        else:
            from tenet.agent import AgentLoop, Proposal, ScriptedBrain
            from tenet.events.audit import audit_trace
            memory, scope, kwargs = tenet(root)
            memory.rebuild_projections()
            audit = audit_trace(memory.log)
            assert not audit.issues, audit.issues
            unknown = len(audit.indeterminate_operations)
            try:
                AgentLoop(**kwargs).run_task(task='fixture', grant=scope, namespace='n',
                    brain=ScriptedBrain([Proposal('fs.write', {'path':'effect','content':'new'})]))
            except ValueError as exc:
                if 'already started' not in str(exc): raise
                rejected = True
            memory.log.close()
        unchanged = exists == (root/'effect').exists()
        return dict(system=system, phase=phase, effect_exists=exists,
                    indeterminate_count=unknown, task_reuse_rejected=rejected,
                    recovery_preserved_effect_state=unchanged,
                    passed=unknown == 1 and rejected and unchanged and exists == (phase == 'after_effect'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--child', nargs=3)
    args = parser.parse_args()
    if args.child:
        child(args.child[0], args.child[1], Path(args.child[2]))
    else:
        print(json.dumps([check(system, phase) for system in ('repaired','conventional')
                          for phase in ('before_effect','after_effect')], indent=2))
