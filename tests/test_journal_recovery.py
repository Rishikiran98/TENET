from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from threading import Barrier
import sqlite3

import pytest

from tenet import MemoryCore, HashingEmbedder
from tenet.agent import AgentLoop, Proposal, ScriptedBrain
from tenet.approver import ScriptedApprover
from tenet.contracts import ToolExecutionError
from tenet.events import SQLiteEventLog, taxonomy as T
from tenet.events.audit import audit_trace
from tenet.gate import DefaultGate
from tenet.journal import JournalExecutor
from tenet.recovery import reconcile_journal
from tenet.scope import ScopeGrant, ToolGrant
from test_review_priorities import Allow


def setup(tmp_path, after=False):
    log = SQLiteEventLog(tmp_path / 'events.db')
    store = JournalExecutor(tmp_path / 'journal.db')
    memory = MemoryCore(HashingEmbedder(), log=log)
    g = ScopeGrant.issue(principal='u', task_id='task', namespaces={'n'},
        tools=(ToolGrant('journal.append', {'journal': 'reports'}),), max_actions=1)
    p = Proposal('journal.append', {'journal': 'reports', 'content': 'result'})
    class Interrupted:
        store_id = store.store_id
        def execute_operation(self, proposal, tg, dispatch):
            if after: store.execute_operation(proposal, tg, dispatch)
            raise ToolExecutionError('interrupted fixture', outcome_unknown=True)
    loop = AgentLoop(memory=memory, gate=DefaultGate(), policy=Allow(),
                     approver=ScriptedApprover([]), executor=Interrupted())
    result = loop.run_task(task='report', grant=g, namespace='n', brain=ScriptedBrain([p]))
    assert not result.completed
    dispatch = next(e for e in log if e.event_type == T.ACTION_DISPATCHED)
    return log, store, p, g, dispatch


@pytest.mark.parametrize('after', [False, True])
def test_reconcile_fences_or_establishes_exact_operation(tmp_path, after):
    log, store, p, g, dispatch = setup(tmp_path, after)
    event = reconcile_journal(log, store, dispatch.payload['operation_id'])
    assert event.payload['state'] == ('applied' if after else 'cancelled')
    assert reconcile_journal(log, store, p_id := dispatch.payload['operation_id']) == event
    assert audit_trace(log).issues == ()
    assert audit_trace(log).indeterminate_operations == ()
    assert sum(e.event_type == T.ACTION_DISPATCHED for e in log) == 1
    if after:
        store.execute_operation(p, g.tools[0], dispatch)
    else:
        with pytest.raises(ToolExecutionError, match='fenced'):
            store.execute_operation(p, g.tools[0], dispatch)
    with sqlite3.connect(tmp_path / 'journal.db') as db:
        assert db.execute("SELECT COUNT(*) FROM journal_operations WHERE state='applied'").fetchone()[0] == int(after)
    memory = MemoryCore(HashingEmbedder(), log=log)
    loop = AgentLoop(memory=memory, gate=DefaultGate(), policy=Allow(), executor=store,
                     approver=ScriptedApprover([]))
    with pytest.raises(ValueError, match='already started'):
        loop.run_task(task='retry', grant=g, namespace='n', brain=ScriptedBrain([p]))
    store.close(); log.close()


def test_concurrent_reconcilers_record_one_resolution(tmp_path):
    log, store, p, g, dispatch = setup(tmp_path, True)
    log.close(); store.close()
    barrier = Barrier(2)
    def worker(_):
        log = SQLiteEventLog(tmp_path / 'events.db')
        store = JournalExecutor(tmp_path / 'journal.db')
        barrier.wait()
        event = reconcile_journal(log, store, dispatch.payload['operation_id'])
        log.close(); store.close()
        return event.event_id
    with ThreadPoolExecutor(2) as pool:
        ids = list(pool.map(worker, range(2)))
    assert ids[0] == ids[1]


def test_cancellation_and_original_worker_race(tmp_path):
    log, store, p, g, dispatch = setup(tmp_path)
    other = JournalExecutor(tmp_path / 'journal.db')
    barrier = Barrier(2)
    def execute():
        barrier.wait()
        try: return store.execute_operation(p, g.tools[0], dispatch)['state']
        except ToolExecutionError: return 'cancelled'
    def recover():
        barrier.wait()
        return reconcile_journal(log, other, dispatch.payload['operation_id']).payload['state']
    with ThreadPoolExecutor(2) as pool:
        a, b = pool.submit(execute), pool.submit(recover)
        assert a.result() == b.result()
    log.close(); store.close(); other.close()


def test_wrong_store_and_reused_id_do_not_settle(tmp_path):
    log, store, p, g, dispatch = setup(tmp_path, True)
    other = JournalExecutor(tmp_path / 'other.db')
    with pytest.raises(ValueError, match='binding'):
        reconcile_journal(log, other, dispatch.payload['operation_id'])
    assert audit_trace(log).indeterminate_operations
    changed = replace(dispatch, correlation_id='other-task')
    with pytest.raises(ValueError, match='evidence mismatch'):
        store.execute_operation(p, g.tools[0], changed)
    log.close(); store.close(); other.close()


@pytest.mark.parametrize('args,constraints', [
    ({'journal':'outside','content':'x'}, {'journal':'reports'}),
    ({'journal':'reports','content':'x'}, {}),
    ({'journal':'reports','content':'x','operation_id':'chosen'}, {'journal':'reports'}),
    ({'journal':'reports','content':'x'*4097}, {'journal':'reports'}),
])
def test_journal_gate_and_executor_reject_invalid_intent(tmp_path, args, constraints):
    log, store, p, g, dispatch = setup(tmp_path)
    proposal = Proposal('journal.append', args)
    grant = replace(g, tools=(ToolGrant('journal.append', constraints),))
    assert not DefaultGate().evaluate(proposal, grant, Allow()).allowed
    with pytest.raises(ToolExecutionError):
        store.execute_operation(proposal, grant.tools[0], dispatch)
    log.close(); store.close()


def test_reconciliation_after_expiry_adds_no_authority(tmp_path):
    log, store, p, g, dispatch = setup(tmp_path, True)
    # Reconciliation never evaluates a policy or invokes execute_operation.
    def forbidden(*args): raise AssertionError('effect replayed')
    store.execute_operation = forbidden
    event = reconcile_journal(log, store, dispatch.payload['operation_id'])
    assert event.payload['state'] == 'applied'
    assert sum(e.event_type == T.ACTION_DISPATCHED for e in log) == g.max_actions
    log.close(); store.close()
