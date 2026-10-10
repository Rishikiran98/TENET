"""Shared deterministic behavioral cases. Run each implementation in a fresh process.

Usage: PYTHONPATH=src python evaluation/compare.py --system repaired
The original runs against an archived source tree via PYTHONPATH.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from conventional import Conventional

CASES = ('allowed_write', 'outside_grant', 'budget', 'stale_approval',
         'approval_expiry', 'prefix_symlink', 'tool_grant_mismatch',
         'task_reuse', 'unknown_citation')


def proposal(tool='fs.write', path='notes/x', content='new', citations=()):
    return dict(tool=tool, args={'path': path, **({'content': content} if tool == 'fs.write' else {})},
                justification='fixture', citations=list(citations))


def grant():
    return dict(task_id='task', grant_id='grant', expiry=2000000010., budget=1,
                tools=[{'tool': 'fs.write', 'constraints': {}}])


class TenetAdapter:
    def __init__(self, root, clock):
        from tenet import MemoryCore, HashingEmbedder
        from tenet.agent import AgentLoop, ScriptedBrain, Proposal
        from tenet.approver import ApprovalDecision
        from tenet.gate import DefaultGate, GateDecision, Verdict
        from tenet.executor import fs_executor
        from tenet.scope import ScopeGrant, ToolGrant
        self.types = ScopeGrant, ToolGrant, Proposal, ScriptedBrain, ApprovalDecision
        self.stack = ExitStack()
        now = lambda: datetime.fromtimestamp(clock(), timezone.utc)
        self.stack.enter_context(patch('tenet.scope.grant._now', now))
        kwargs = {'clock': now} if 'clock' in inspect.signature(MemoryCore).parameters else {}
        self.memory = MemoryCore(HashingEmbedder(), **kwargs)
        self.executor = fs_executor(root)
        class Policy:
            version = 'fixture/v1'
            def evaluate(self, p, g, t):
                verdict = Verdict.ESCALATE if p.tool == 'fs.delete' else Verdict.ALLOW
                return GateDecision(verdict, ('fixture',), 'shared fixture')
        self.loop_class = AgentLoop
        self.loop_args = dict(memory=self.memory, gate=DefaultGate(), policy=Policy(), executor=self.executor)
        if 'clock' in inspect.signature(AgentLoop).parameters: self.loop_args['clock'] = now

    def convert_grant(self, g):
        ScopeGrant, ToolGrant, *_ = self.types
        return ScopeGrant(grant_id=g['grant_id'], principal='fixture', task_id=g['task_id'],
            namespaces=frozenset({'n'}), tools=tuple(ToolGrant(**t) for t in g['tools']),
            max_actions=g['budget'], expires_at=datetime.fromtimestamp(g['expiry'], timezone.utc).isoformat())

    def convert_proposal(self, p):
        return self.types[2](p['tool'], p['args'], p['justification'], tuple(p['citations']))

    def run(self, g, proposals, *, approve=None):
        _, _, _, Brain, Decision = self.types
        class Approver:
            def decide(self, surface):
                approved, token = approve(getattr(surface, 'binding', 'unbound')) if approve else (False, '')
                kw = {'binding': token} if 'binding' in inspect.signature(Decision).parameters else {}
                return Decision(approved, 'fixture', **kw)
        loop = self.loop_class(**self.loop_args, approver=Approver())
        return loop.run_task(task='fixture task', grant=self.convert_grant(g), namespace='n',
                             brain=Brain([self.convert_proposal(p) for p in proposals]))

    def execute(self, p, tg):
        return self.executor.execute(self.convert_proposal(p), self.types[1](**tg))

    def close(self): self.stack.close()


def case(name, system):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory); (root/'notes').mkdir(); (root/'protected').mkdir()
        (root/'notes/x').write_text('original'); (root/'protected/x').write_text('protected')
        now = [2000000000.]
        adapter = (Conventional(root, clock=lambda: now[0]) if system == 'conventional'
                   else TenetAdapter(root, lambda: now[0]))
        g = grant(); p = proposal(); error = None
        try:
            if name == 'allowed_write':
                adapter.run(g, [p]); passed = (root/'notes/x').read_text() == 'new'
            elif name == 'outside_grant':
                g['tools'] = [{'tool':'fs.read'}]
                adapter.run(g, [p]); passed = (root/'notes/x').read_text() == 'original'
            elif name == 'budget':
                adapter.run(g, [p, proposal(content='second')]); passed = (root/'notes/x').read_text() == 'new'
            elif name in {'stale_approval','approval_expiry'}:
                g['tools'] = [{'tool':'fs.delete'}]
                def approval(token):
                    if name == 'approval_expiry': now[0] += 20
                    return True, 'wrong' if name == 'stale_approval' else token
                adapter.run(g, [proposal('fs.delete')], approve=approval)
                passed = (root/'notes/x').exists()
            elif name == 'prefix_symlink':
                (root/'notes/link').symlink_to(root/'protected', target_is_directory=True)
                g['tools'][0]['constraints'] = {'path_prefix':'/notes'}
                adapter.run(g, [proposal(path='notes/link/x')])
                passed = (root/'protected/x').read_text() == 'protected'
            elif name == 'tool_grant_mismatch':
                try: adapter.execute(proposal('fs.delete'), {'tool':'fs.read'})
                except Exception: pass
                passed = (root/'notes/x').exists()
            elif name == 'task_reuse':
                adapter.run(g, [p])
                rejected = False
                try: adapter.run(g, [proposal(content='second')])
                except Exception as exc:
                    import sqlite3
                    rejected = (isinstance(exc, sqlite3.IntegrityError) or
                                isinstance(exc, ValueError) and 'already started' in str(exc))
                passed = rejected and (root/'notes/x').read_text() == 'new'
            elif name == 'unknown_citation':
                adapter.run(g, [proposal(citations=('missing',))])
                passed = (root/'notes/x').read_text() == 'original'
        except Exception as exc:
            passed, error = False, f'{type(exc).__name__}: {exc}'
        finally:
            adapter.close()
        return {'case':name, 'passed':passed, 'unexpected_error':error}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--system', choices=['original','repaired','conventional'], required=True)
    args = parser.parse_args()
    results = [case(name, args.system) for name in CASES]
    print(json.dumps({'system':args.system, 'cases':results,
                      'passed':sum(r['passed'] for r in results), 'total':len(results)}, indent=2))
