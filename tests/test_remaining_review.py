"""Remaining review regressions: receipts, feedback, storage, and validation."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import sqlite3

import pytest

from tenet import MemoryCore, HashingEmbedder
from tenet.agent import AgentLoop, Proposal, ScriptedBrain
from tenet.approver import ScriptedApprover
from tenet.events import Actor, InMemoryEventLog, SQLiteEventLog, ChainError, taxonomy as T
from tenet.executor import fs_executor
from tenet.gate import DefaultGate, GateDecision, Verdict
from tenet.scope import ScopeGrant, ToolGrant


class Allow:
    version = 'review/v2'
    def evaluate(self, *args): return GateDecision(Verdict.ALLOW, (), 'fixture')


def scope(tool='fs.read', constraints=None):
    return ScopeGrant.issue(principal='test', task_id='t', namespaces={'n'},
                           tools=(ToolGrant(tool, constraints or {}),), max_actions=4)


def loop(memory, executor):
    return AgentLoop(memory=memory, gate=DefaultGate(), policy=Allow(),
                     approver=ScriptedApprover([]), executor=executor)


@pytest.mark.parametrize('args', [{}, {'path':None}, {'path':42}, {'path':'../x'},
    {'path':r'notes/a\..\..\protected'}, {'path':'notes/s', 'dest':'/protected/x'},
    {'path':'C:/protected'}, {'path':'notes/s:stream'}])
@pytest.mark.parametrize('constraints', [{}, {'path_prefix':'/notes'}])
def test_filesystem_args_always_validated(args, constraints):
    decision = DefaultGate().evaluate(Proposal('fs.read', args), scope(constraints=constraints), Allow())
    assert decision.verdict is Verdict.DENY


@pytest.mark.parametrize('args', [{'path':'notes/s'}, {'path':'notes/s','content':42},
                                 {'path':'notes/s','content':'ok','dest':'protected/x'}])
def test_write_schema_is_checked_before_dispatch(args):
    assert DefaultGate().evaluate(Proposal('fs.write', args), scope('fs.write'), Allow()).verdict is Verdict.DENY


@pytest.mark.parametrize('event_type,payload,missing', [
    (T.ACTION_DISPATCHED, {'operation_id':'p','proposal_digest':'a'*64,'grant_id':'g',
                          'accepted_at':'2026-01-01T00:00:00+00:00'}, 'accepted_at'),
    (T.APPROVAL_REQUESTED, {'proposal_ref':'p','binding':'a'*64}, 'binding'),
    (T.APPROVAL_DECIDED, {'proposal_ref':'p','decision':'approve','approver_id':'u','binding':'a'*64}, 'binding'),
    (T.ACTION_EXECUTED, {'operation_id':'p','proposal_digest':'a'*64,'grant_id':'g',
                        'result':{},'exit_status':'ok'}, 'operation_id'),
    (T.ACTION_FAILED, {'operation_id':'p','proposal_digest':'a'*64,'grant_id':'g','error':'x'}, 'operation_id'),
])
def test_receipt_fields_required_on_append(event_type, payload, missing):
    del payload[missing]
    with pytest.raises(ValueError):
        InMemoryEventLog().append(namespace='n', actor=Actor('system','test'), event_type=event_type,
                                 payload=payload, correlation_id='t')


def test_invalid_receipt_time_rejected():
    with pytest.raises(ValueError):
        InMemoryEventLog().append(namespace='n', actor=Actor('system','test'), event_type=T.ACTION_DISPATCHED,
            payload={'operation_id':'p','proposal_digest':'a'*64,'grant_id':'g','accepted_at':'yesterday'}, correlation_id='t')


def test_brain_adapts_to_block_failure_and_read_result(tmp_path):
    (tmp_path/'notes').mkdir(); (tmp_path/'notes/x').write_text('answer')
    seen = []
    class Adaptive:
        def propose(self, task, context, *, observations):
            seen.append(observations)
            if not observations: return Proposal('fs.read', {'path':'protected/x'})
            last = observations[-1]
            if last.outcome_event_type == T.ACTION_BLOCKED:
                return Proposal('fs.read', {'path':'notes/missing'})
            if last.outcome_event_type == T.ACTION_FAILED:
                assert last.error
                return Proposal('fs.read', {'path':'notes/x'})
            assert last.result['content'] == 'answer'
            with pytest.raises(TypeError): last.result['content'] = 'mutated'
            return None
    memory = MemoryCore(HashingEmbedder())
    result = loop(memory, fs_executor(tmp_path)).run_task(task='adapt', namespace='n',
        grant=scope(constraints={'path_prefix':'/notes'}), brain=Adaptive())
    assert result.completed and len(seen) == 4
    assert tuple(map(len, seen)) == (0, 1, 2, 3)
    assert result.steps[-1].observation.result['content'] == 'answer'


def test_read_is_bounded_and_log_omits_content(tmp_path):
    text = '€' * 3000 + 'NEVER_PERSIST_THIS_TAIL'
    (tmp_path/'x').write_text(text)
    memory = MemoryCore(HashingEmbedder())
    result = loop(memory, fs_executor(tmp_path)).run_task(task='read', namespace='n', grant=scope(),
        brain=ScriptedBrain([Proposal('fs.read', {'path':'x'})]))
    receipt = next(e for e in memory.log if e.event_type == T.ACTION_EXECUTED)
    assert 'content' not in receipt.payload['result']
    observation = result.steps[0].observation.result
    assert observation['truncated']
    assert len(observation['content'].encode()) <= 4096
    assert observation['content'] == '€' * (4096 // 3)
    assert receipt.payload['result']['content_sha256'] == hashlib.sha256(observation['content'].encode()).hexdigest()
    assert 'NEVER_PERSIST_THIS_TAIL' not in repr(tuple(memory.log))


def test_reopened_memory_hydrates_without_manual_rebuild(tmp_path):
    path = tmp_path/'events.db'; log = SQLiteEventLog(path)
    memory = MemoryCore(HashingEmbedder(), log=log); memory.ingest('hello', 'n', 'fixture'); log.close()
    reopened = SQLiteEventLog(path)
    memory = MemoryCore(HashingEmbedder(), log=reopened)
    assert memory.retrieve('hello', scope())[0].record.text == 'hello'
    reopened.close()


def test_hydration_failure_is_explicit(tmp_path):
    path = tmp_path/'events.db'; log = SQLiteEventLog(path)
    MemoryCore(HashingEmbedder(), log=log).ingest('hello', 'n', 'fixture'); log.close()
    class Broken:
        version = 'broken'
        def contextualize(self, raw): raise RuntimeError('embedding unavailable')
    reopened = SQLiteEventLog(path)
    with pytest.raises(RuntimeError, match='embedding unavailable'):
        MemoryCore(HashingEmbedder(), log=reopened, contextualizer=Broken())
    reopened.close()


def test_sqlite_appends_do_not_reparse_history(tmp_path, monkeypatch):
    log = SQLiteEventLog(tmp_path/'events.db')
    import tenet.events.sqlite as implementation
    real = implementation.json.loads; calls = []
    def counted(value): calls.append(1); return real(value)
    monkeypatch.setattr(implementation.json, 'loads', counted)
    for i in range(100):
        log.append(namespace='n', actor=Actor('user','u'), event_type=T.TASK_COMPLETED,
                   payload={'summary':str(i)}, correlation_id='t')
    assert len(calls) <= 100, 'append must not decode all historical rows'
    log.close()


def test_incremental_load_handles_other_writers_and_rollback(tmp_path):
    path = tmp_path/'events.db'; a = SQLiteEventLog(path); b = SQLiteEventLog(path)
    def append(log, text):
        return log.append(namespace='n', actor=Actor('user','u'), event_type=T.TASK_COMPLETED,
                          payload={'summary':text}, correlation_id='t')
    first = append(a, 'first'); assert list(b)[0] == first
    with a.transaction():
        second = append(a, 'second')
        try:
            with a.transaction():
                append(a, 'rolled back'); raise RuntimeError('rollback savepoint')
        except RuntimeError: pass
        third = append(a, 'third')
    assert [e.event_id for e in b] == [first.event_id, second.event_id, third.event_id]
    append(b, 'fourth')
    assert a.verify() and len(a) == 4
    a.close(); b.close()


def test_verify_reloads_existing_rows_even_with_incremental_cache(tmp_path):
    path = tmp_path/'events.db'; log = SQLiteEventLog(path)
    log.append(namespace='n', actor=Actor('user','u'), event_type=T.TASK_COMPLETED,
               payload={'summary':'original'}, correlation_id='t')
    with sqlite3.connect(path) as db:
        document = json.loads(db.execute('SELECT document FROM events').fetchone()[0])
        document['payload']['summary'] = 'tampered'
        db.execute('UPDATE events SET document=?', (json.dumps(document),))
    with pytest.raises(ChainError): log.verify()
    log.close()


def test_legacy_schema_is_readable_but_not_newly_writable(tmp_path):
    from tenet.events.envelope import Event, GENESIS_HASH, compute_hash
    from tenet.events.audit import audit_trace
    from tenet.values import canonical
    fields = dict(event_id='legacy', ts='2026-01-01T00:00:00+00:00', namespace='n',
                  actor=Actor('tool','fs.read'), event_type=T.ACTION_EXECUTED,
                  payload={'result':{'content':'old content'},'exit_status':'ok'},
                  schema_version=1, correlation_id='old-task', causation_id=None, prev_hash=GENESIS_HASH)
    event = Event(**fields, hash=compute_hash(**fields))
    document = {**fields, 'hash':event.hash, 'actor':{'kind':'tool','id':'fs.read'}}
    path = tmp_path/'legacy.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE events (seq INTEGER PRIMARY KEY, document TEXT NOT NULL)')
        db.execute('INSERT INTO events(document) VALUES (?)', (canonical(document),))
    log = SQLiteEventLog(path)
    assert log.verify() and list(log)[0] == event
    assert any('legacy schema' in issue for issue in audit_trace(log).issues)
    with pytest.raises(ValueError, match='schema_version'):
        log.append(namespace='n', actor=Actor('tool','fs.read'), event_type=T.ACTION_EXECUTED,
                   payload=fields['payload'], correlation_id='old-task', schema_version=1)
    log.close()


def test_live_escalation_trace_and_denial_after_policy_change_audit_cleanly(tmp_path):
    from tenet.approver import ApprovalDecision
    from tenet.events.audit import audit_trace
    for change in (False, True):
        class Escalate:
            version = 'v1'
            def evaluate(self, *args): return GateDecision(Verdict.ESCALATE, (), 'fixture')
        policy = Escalate()
        class Approver:
            def decide(self, surface):
                if change: policy.version = 'v2'
                return ApprovalDecision(True, 'u', binding=surface.binding)
        (tmp_path/'x').write_text('answer')
        memory = MemoryCore(HashingEmbedder())
        agent = AgentLoop(memory=memory, gate=DefaultGate(), policy=policy,
                          approver=Approver(), executor=fs_executor(tmp_path))
        result = agent.run_task(task='read', grant=scope(), namespace='n',
                               brain=ScriptedBrain([Proposal('fs.read', {'path':'x'})]))
        assert result.steps[0].outcome_event_type == (T.ACTION_BLOCKED if change else T.ACTION_EXECUTED)
        assert audit_trace(memory.log).issues == ()


@pytest.mark.parametrize('data', [b'x' * 4096, b'x' * 4097, b'\xff', b'x' * 4095 + '€'.encode()])
def test_bounded_read_utf8_boundary_and_invalid_encoding(tmp_path, data):
    (tmp_path/'x').write_bytes(data)
    from tenet.contracts import ToolExecutionError
    if data == b'\xff':
        with pytest.raises(ToolExecutionError):
            fs_executor(tmp_path).execute(Proposal('fs.read', {'path':'x'}), ToolGrant('fs.read'))
    else:
        result = fs_executor(tmp_path).execute(Proposal('fs.read', {'path':'x'}), ToolGrant('fs.read'))
        assert len(result['content'].encode()) <= 4096
        assert result['truncated'] == (len(data) > 4096)


def test_extra_destination_and_windows_paths_are_also_rejected_by_executor(tmp_path):
    from tenet.contracts import ToolExecutionError
    executor = fs_executor(tmp_path)
    for args in ({'path':'notes/x','dest':'protected/x'}, {'path':r'notes\..\protected'}, {'path':'C:/x'}):
        with pytest.raises(ToolExecutionError): executor.execute(Proposal('fs.read', args), ToolGrant('fs.read'))


def test_new_filesystem_tool_requires_its_own_schema():
    proposal = Proposal('fs.copy', {'path':'notes/x','dest':'protected/x'})
    assert DefaultGate().evaluate(proposal, scope('fs.copy'), Allow()).verdict is Verdict.DENY


def test_custom_read_executor_cannot_persist_unbounded_content(tmp_path):
    class Custom:
        def execute(self, proposal, grant): return {'content':'sensitive' * 10000, 'extra':'do not persist'}
    memory = MemoryCore(HashingEmbedder())
    result = loop(memory, Custom()).run_task(task='read', namespace='n', grant=scope(),
        brain=ScriptedBrain([Proposal('fs.read', {'path':'x'})]))
    assert len(result.steps[0].observation.result['content'].encode()) == 4096
    receipt = next(e for e in memory.log if e.event_type == T.ACTION_EXECUTED)
    assert 'content' not in receipt.payload['result'] and 'extra' not in receipt.payload['result']


def test_invalid_unconstrained_arguments_preserve_budget(tmp_path):
    (tmp_path/'x').write_text('answer')
    memory = MemoryCore(HashingEmbedder()); grant = replace(scope(), max_actions=1)
    result = loop(memory, fs_executor(tmp_path)).run_task(task='read', namespace='n', grant=grant,
        brain=ScriptedBrain([Proposal('fs.read'), Proposal('fs.read', {'path':'x'})]))
    assert [s.outcome_event_type for s in result.steps] == [T.ACTION_BLOCKED, T.ACTION_EXECUTED]


@pytest.mark.parametrize('path', ['notes/.. /protected', 'notes/../protected.'])
def test_ambiguous_native_path_components_are_denied(path):
    proposal = Proposal('fs.read', {'path':path})
    assert DefaultGate().evaluate(proposal, scope(), Allow()).verdict is Verdict.DENY


def test_invalid_executor_result_yields_indeterminate_observation():
    class BadResult:
        def execute(self, proposal, grant): return {'binary':b'not JSON'}
    memory = MemoryCore(HashingEmbedder())
    result = loop(memory, BadResult()).run_task(task='read', namespace='n', grant=scope(),
        brain=ScriptedBrain([Proposal('fs.read', {'path':'x'})]))
    assert not result.completed
    observation = result.steps[0].observation
    assert observation.outcome_event_type == T.ACTION_INDETERMINATE
    assert observation.result is None and observation.error
