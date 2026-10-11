"""A bounded transactional report journal with durable operation fencing.

The SQLite row is both the appended report and its authoritative operation
receipt. This contract does not apply to ordinary filesystem writes.
"""
from contextlib import contextmanager
import hashlib
import sqlite3
from threading import RLock
from uuid import uuid4

from .constraints import check_constraints, ConstraintViolation
from .contracts import ToolExecutionError
from .events import taxonomy as T
from .values import digest, freeze


def operation_binding(dispatch):
    return digest({'operation_id': dispatch.payload['operation_id'],
                   'proposal_digest': dispatch.payload['proposal_digest'],
                   'grant_id': dispatch.payload['grant_id'],
                   'task_id': dispatch.correlation_id, 'namespace': dispatch.namespace,
                   'executor_id': dispatch.payload['executor_id']})


def journal_evidence(proposal, dispatch, state):
    content = proposal.args['content'].encode('utf-8')
    return {'binding': operation_binding(dispatch), 'state': state,
            'journal': proposal.args['journal'],
            'content_sha256': hashlib.sha256(content).hexdigest(),
            'bytes_written': len(content) if state == 'applied' else 0}


class JournalExecutor:
    """Trusted store, optionally composing the existing filesystem executor.

    A cancelled operation never executes, even if the original worker arrives
    later. No caller-supplied operation ID from tool arguments is accepted.
    """

    def __init__(self, path, *, fallback=None):
        self._lock = RLock()
        self._db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute('PRAGMA synchronous=FULL')
        self._db.executescript('''
            CREATE TABLE IF NOT EXISTS journal_identity (slot INTEGER PRIMARY KEY CHECK(slot=1), id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS journal_operations (
                operation_id TEXT PRIMARY KEY, binding TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('applied','cancelled')),
                journal TEXT NOT NULL, content TEXT,
                CHECK((state='applied' AND content IS NOT NULL) OR
                      (state='cancelled' AND content IS NULL)));
        ''')
        self._db.execute('INSERT OR IGNORE INTO journal_identity VALUES (1, ?)', (uuid4().hex,))
        self.store_id = self._db.execute('SELECT id FROM journal_identity WHERE slot=1').fetchone()[0]
        self._fallback = fallback

    @contextmanager
    def _transaction(self):
        with self._lock:
            self._db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self._db.execute('COMMIT')
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute('ROLLBACK')
                raise

    def execute(self, proposal, tool_grant):
        if proposal.tool == 'journal.append' or self._fallback is None:
            raise ToolExecutionError('journal execution requires a committed dispatch identity')
        return self._fallback.execute(proposal, tool_grant)

    def _validate(self, proposal, tool_grant, dispatch):
        if proposal.tool != 'journal.append' or tool_grant.tool != proposal.tool:
            raise ToolExecutionError('journal tool/grant mismatch')
        try:
            check_constraints(proposal.tool, proposal.args, tool_grant.constraints)
        except ConstraintViolation as exc:
            raise ToolExecutionError(str(exc)) from exc
        if (dispatch.event_type != T.ACTION_DISPATCHED or
                dispatch.payload.get('executor_id') != self.store_id or
                dispatch.payload['proposal_digest'] != proposal.digest):
            raise ValueError('dispatch/store binding mismatch')

    def _settle(self, proposal, tool_grant, dispatch, *, cancel):
        self._validate(proposal, tool_grant, dispatch)
        op, bound = dispatch.payload['operation_id'], operation_binding(dispatch)
        with self._transaction():
            row = self._db.execute(
                'SELECT binding,state,journal,content FROM journal_operations WHERE operation_id=?',
                (op,)).fetchone()
            if row is None:
                state = 'cancelled' if cancel else 'applied'
                self._db.execute('INSERT INTO journal_operations VALUES (?,?,?,?,?)',
                    (op, bound, state, proposal.args['journal'], None if cancel else proposal.args['content']))
            else:
                previous, state, journal, content = row
                if (previous != bound or journal != proposal.args['journal'] or
                        state == 'applied' and content != proposal.args['content'] or
                        state == 'cancelled' and content is not None):
                    raise ValueError('journal operation evidence mismatch')
            result = journal_evidence(proposal, dispatch, state)
        return freeze(result)

    def execute_operation(self, proposal, tool_grant, dispatch):
        result = self._settle(proposal, tool_grant, dispatch, cancel=False)
        if result['state'] == 'cancelled':
            raise ToolExecutionError('operation fenced by reconciliation')
        return result

    def reconcile(self, proposal, tool_grant, dispatch):
        """Inspect applied evidence or fence absence. Never append a report."""
        return self._settle(proposal, tool_grant, dispatch, cancel=True)

    def close(self):
        with self._lock:
            self._db.close()
