"""Regressions from review: public imports and pre-dispatch path authority."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tenet import MemoryCore, HashingEmbedder
from tenet.agent import AgentLoop, Proposal, ScriptedBrain
from tenet.approver import ScriptedApprover
from tenet.events import taxonomy as T
from tenet.events.audit import audit_trace
from tenet.executor import fs_executor, SandboxViolation
from tenet.gate import DefaultGate, GateDecision, Verdict
from tenet.scope import ScopeGrant, ToolGrant


@pytest.mark.parametrize('first', ['tenet.gate', 'tenet.executor', 'tenet.agent',
                                  'tenet.gate.contract', 'tenet.executor.fs_tools'])
def test_public_imports_in_fresh_interpreter(first):
    env = {**os.environ, 'PYTHONPATH':str(Path(__file__).parents[1] / 'src')}
    result = subprocess.run([sys.executable, '-c',
        f'import {first}; import tenet.agent; import tenet.gate; import tenet.executor'],
        env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


class Allow:
    version = 'review-fixture/v1'
    def evaluate(self, *args):
        return GateDecision(Verdict.ALLOW, ('fixture',), 'allow fixture')


class MustNotRun:
    version = 'must-not-run/v1'
    def evaluate(self, *args):
        raise AssertionError('policy was consulted for an out-of-grant proposal')


def scoped(tool='fs.delete', constraints=None):
    return ScopeGrant.issue(principal='test', task_id='task', namespaces={'n'},
        tools=(ToolGrant(tool, {'path_prefix':'/notes'} if constraints is None else constraints),),
        max_actions=1)


@pytest.mark.parametrize('tool', ['fs.read', 'fs.write', 'fs.delete'])
@pytest.mark.parametrize('path', ['/protected/s', '/notes2/s', '/notes/../protected/s',
                                 '', '/', '//notes/s', None])
def test_path_outside_grant_denied_before_policy(tool, path):
    decision = DefaultGate().evaluate(Proposal(tool, {'path':path}), scoped(tool), MustNotRun())
    assert decision.verdict is Verdict.DENY
    assert decision.matched_rule_ids == ()


@pytest.mark.parametrize('constraints', [{'path_prefix':None}, {'path_prefix':''},
                                        {'path_prefix':'/notes/..'}, {'unknown':True}])
def test_unsupported_or_malformed_constraints_denied_before_policy(constraints):
    decision = DefaultGate().evaluate(Proposal('fs.delete', {'path':'/notes/s'}),
                                     scoped(constraints=constraints), MustNotRun())
    assert decision.verdict is Verdict.DENY


@pytest.mark.parametrize('path', ['/notes/s', 'notes/s', '/notes/sub/s'])
def test_in_prefix_path_reaches_policy(path):
    decision = DefaultGate().evaluate(Proposal('fs.delete', {'path':path}), scoped(), Allow())
    assert decision.verdict is Verdict.ALLOW


def test_out_of_prefix_block_preserves_budget_for_valid_action(tmp_path):
    (tmp_path/'protected').mkdir(); (tmp_path/'notes').mkdir()
    (tmp_path/'protected/s').write_text('secret'); (tmp_path/'notes/s').write_text('delete me')
    memory = MemoryCore(HashingEmbedder())
    loop = AgentLoop(memory=memory, gate=DefaultGate(), policy=Allow(),
                     approver=ScriptedApprover([]), executor=fs_executor(tmp_path))
    result = loop.run_task(task='delete allowed note', grant=scoped(), namespace='n',
        brain=ScriptedBrain([Proposal('fs.delete', {'path':'/protected/s'}),
                            Proposal('fs.delete', {'path':'/notes/s'})]))
    assert [s.outcome_event_type for s in result.steps] == [T.ACTION_BLOCKED, T.ACTION_EXECUTED]
    assert result.steps[0].decision.verdict is Verdict.DENY
    assert (tmp_path/'protected/s').read_text() == 'secret'
    assert not (tmp_path/'notes/s').exists()
    events = list(memory.log)
    assert sum(e.event_type == T.ACTION_DISPATCHED for e in events) == 1
    assert events[-1].payload['actions_used'] == 1
    assert not audit_trace(memory.log).issues


def test_audit_identifies_out_of_prefix_dispatch_from_permissive_custom_gate(tmp_path):
    class BadGate:
        def evaluate(self, *args, **kwargs):
            return GateDecision(Verdict.ALLOW, (), 'deliberately wrong fixture')
    (tmp_path/'protected').mkdir(); (tmp_path/'protected/s').write_text('secret')
    memory = MemoryCore(HashingEmbedder())
    loop = AgentLoop(memory=memory, gate=BadGate(), policy=Allow(),
                     approver=ScriptedApprover([]), executor=fs_executor(tmp_path))
    loop.run_task(task='bad gate fixture', grant=scoped(), namespace='n',
                  brain=ScriptedBrain([Proposal('fs.delete', {'path':'/protected/s'})]))
    assert (tmp_path/'protected/s').exists()
    assert any('constraint violation' in issue for issue in audit_trace(memory.log).issues)
