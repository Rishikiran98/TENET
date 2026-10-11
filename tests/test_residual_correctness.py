import pytest

from tenet import MemoryCore, HashingEmbedder
from tenet.events import SQLiteEventLog, taxonomy as T
from tenet.events.audit import audit_trace
from test_correctness import runner, run, grant, Recorder
from test_causal_audit import Trace


def test_outer_rollback_cannot_erase_dispatch_after_effect(tmp_path):
    log = SQLiteEventLog(tmp_path / 'events.db')
    memory = MemoryCore(HashingEmbedder(), log=log)
    executor = Recorder()
    with pytest.raises(RuntimeError), log.transaction():
        run(runner(memory, executor), grant())
        raise RuntimeError('caller rolls back')
    assert executor.calls == []
    assert len(log) == 0
    log.close()


@pytest.mark.parametrize('corrupt', ['digest', 'task', 'cause'])
def test_invalid_outcome_does_not_resolve_uncertainty(corrupt):
    t = Trace(); proposal = t.propose('a')
    initial = t.verdict(proposal, proposal)
    final = t.verdict(proposal, initial, phase='dispatch')
    dispatch = t.dispatch(proposal, final)
    payload = dict(operation_id=proposal.event_id,
                   proposal_digest=proposal.payload['proposal_digest'],
                   grant_id=t.grant.grant_id, result={}, exit_status='ok')
    if corrupt == 'digest': payload['proposal_digest'] = '0' * 64
    t.emit(T.ACTION_EXECUTED, payload,
           proposal if corrupt == 'cause' else dispatch,
           task='other' if corrupt == 'task' else 't')
    report = audit_trace(t.log)
    assert report.issues
    assert report.indeterminate_operations == (proposal.event_id,)


def test_task_authority_cannot_be_borrowed_across_namespace():
    t = Trace()
    def emit(kind, payload, cause=None, task='t'):
        from tenet.events import Actor
        return t.log.append(namespace='other', actor=Actor('system', 'fixture'),
                            event_type=kind, payload=payload, correlation_id=task,
                            causation_id=cause.event_id if cause else None)
    t.emit = emit
    p = t.propose('a'); initial = t.verdict(p, p)
    final = t.verdict(p, initial, phase='dispatch'); t.dispatch(p, final)
    assert any('task/proposal authority' in issue for issue in audit_trace(t.log).issues)
