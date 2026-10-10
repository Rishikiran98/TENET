from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from tenet import MemoryCore, HashingEmbedder
from tenet.events import SQLiteEventLog, Actor, taxonomy as T
from test_correctness import runner, run, grant, Recorder


def test_reopen_rebuild_and_task_reuse(tmp_path):
    path = tmp_path / 'events.db'
    log = SQLiteEventLog(path)
    m = MemoryCore(HashingEmbedder(), log=log)
    raw = m.ingest('hello', 'n', 'fixture', ingest_id='one')
    g = grant(); ex = Recorder(); run(runner(m, ex), g)
    before = [e.hash for e in log]; log.close()
    reopened = SQLiteEventLog(path)
    m2 = MemoryCore(HashingEmbedder(), log=reopened)
    m2.rebuild_projections()
    assert m2.raw(raw.raw_id).content == 'hello'
    assert [e.hash for e in reopened] == before
    with pytest.raises(ValueError, match='already started'): run(runner(m2, ex), g)
    assert len(ex.calls) == 1
    reopened.close()


def test_separate_connections_claim_once(tmp_path):
    logs = [SQLiteEventLog(tmp_path / 'events.db') for _ in range(2)]
    barrier = Barrier(2); g = grant(); ex = Recorder()
    def attempt(log):
        m = MemoryCore(HashingEmbedder(), log=log)
        barrier.wait()
        try: run(runner(m, ex), g); return True
        except ValueError: return False
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(attempt, logs)) == [False, True]
    assert len(ex.calls) == 1 and logs[0].verify()
    for log in logs: log.close()


def test_nested_transaction_rollback_is_durable(tmp_path):
    path = tmp_path / 'events.db'; log = SQLiteEventLog(path)
    with pytest.raises(RuntimeError), log.transaction():
        log.append(namespace='n', actor=Actor('user','u'), event_type=T.TASK_COMPLETED,
                   payload={'summary':'not committed'}, correlation_id='t')
        raise RuntimeError('rollback')
    assert len(log) == 0
    log.close(); log = SQLiteEventLog(path)
    assert len(log) == 0
    log.close()
