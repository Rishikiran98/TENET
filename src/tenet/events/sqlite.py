"""Durable log with incremental append loading and explicit full verification."""
from contextlib import contextmanager
import json
import sqlite3
from .envelope import Actor, Event, event_hash, GENESIS_HASH
from .log import InMemoryEventLog, ChainError
from .taxonomy import validate_payload
from ..values import canonical


class SQLiteEventLog(InMemoryEventLog):
    def __init__(self, path):
        super().__init__()
        self._db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute('PRAGMA synchronous=FULL')
        self._db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, document TEXT NOT NULL)')
        self._depth, self._last_seq = 0, 0
        try:
            self.verify()
        except BaseException:
            self._db.close()
            raise

    @staticmethod
    def _decode(document):
        value = json.loads(document)
        value['actor'] = Actor(**value['actor'])
        return Event(**value)

    def _load_all(self):
        rows = list(self._db.execute('SELECT seq, document FROM events ORDER BY seq'))
        self._events = [self._decode(doc) for _, doc in rows]
        self._event_ids = {e.event_id for e in self._events}
        self._last_seq = rows[-1][0] if rows else 0

    def _refresh(self):
        # Only rows appended since this connection last synchronized are decoded.
        rows = self._db.execute('SELECT seq, document FROM events WHERE seq > ? ORDER BY seq',
                                (self._last_seq,)).fetchall()
        events = []
        previous = self._events[-1].hash if self._events else GENESIS_HASH
        new_ids = set()
        for seq, document in rows:
            event = self._decode(document)
            try:
                validate_payload(event.event_type, event.payload, event.schema_version)
            except ValueError as exc:
                raise ChainError(f'invalid incremental schema at sequence {seq}') from exc
            if (event.prev_hash != previous or event_hash(event) != event.hash or
                    event.event_id in self._event_ids or event.event_id in new_ids):
                raise ChainError(f'invalid incremental event at sequence {seq}')
            events.append(event)
            new_ids.add(event.event_id)
            previous = event.hash
        self._events.extend(events)
        self._event_ids.update(new_ids)
        if rows: self._last_seq = rows[-1][0]

    @contextmanager
    def transaction(self):
        with self._lock:
            outer = self._depth == 0
            savepoint = f'level_{self._depth}'
            self._db.execute('BEGIN IMMEDIATE' if outer else f'SAVEPOINT {savepoint}')
            self._depth += 1
            try:
                if outer: self._refresh()
                yield
                self._db.execute('COMMIT' if outer else f'RELEASE {savepoint}')
            except BaseException:
                if outer:
                    if self._db.in_transaction: self._db.execute('ROLLBACK')
                else:
                    self._db.execute(f'ROLLBACK TO {savepoint}')
                    self._db.execute(f'RELEASE {savepoint}')
                # Failure is uncommon. Restore exactly the database's surviving
                # state, including an outer transaction's uncommitted writes.
                self._load_all()
                raise
            finally:
                self._depth -= 1

    def _persist(self, event):
        document = {name: getattr(event, name) for name in event.__dataclass_fields__}
        document['actor'] = {'kind':event.actor.kind, 'id':event.actor.id}
        cursor = self._db.execute('INSERT INTO events(document) VALUES (?)', (canonical(document),))
        self._last_seq = cursor.lastrowid

    def __iter__(self):
        with self._lock:
            if not self._depth: self._refresh()
            return iter(tuple(self._events))

    def __len__(self):
        with self._lock:
            if not self._depth: self._refresh()
            return len(self._events)

    def head_hash(self):
        with self._lock:
            if not self._depth: self._refresh()
            return super().head_hash()

    def verify(self):
        # Never certify only the cache: reread old rows to detect disk tampering.
        with self.transaction():
            self._load_all()
            return super().verify()

    def close(self):
        with self._lock: self._db.close()
