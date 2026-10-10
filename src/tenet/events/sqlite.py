"""Small durable event log. SQLite transactions serialize task claims and appends."""
from contextlib import contextmanager
import json
import sqlite3
from .envelope import Actor, Event
from .log import InMemoryEventLog
from ..values import canonical


class SQLiteEventLog(InMemoryEventLog):
    def __init__(self, path):
        super().__init__()
        self._db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute('PRAGMA synchronous=FULL')
        self._db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, document TEXT NOT NULL)')
        self._depth = 0
        self._load()
        self.verify()

    def _load(self):
        events = []
        for (document,) in self._db.execute('SELECT document FROM events ORDER BY seq'):
            value = json.loads(document)
            value['actor'] = Actor(**value['actor'])
            events.append(Event(**value))
        self._events = events

    @contextmanager
    def transaction(self):
        with self._lock:
            outer = self._depth == 0
            savepoint = f'level_{self._depth}'
            self._db.execute('BEGIN IMMEDIATE' if outer else f'SAVEPOINT {savepoint}')
            self._depth += 1
            try:
                self._load()
                yield
                self._db.execute('COMMIT' if outer else f'RELEASE {savepoint}')
            except BaseException:
                if outer:
                    if self._db.in_transaction:
                        self._db.execute('ROLLBACK')
                else:
                    self._db.execute(f'ROLLBACK TO {savepoint}')
                    self._db.execute(f'RELEASE {savepoint}')
                self._load()
                raise
            finally:
                self._depth -= 1

    def _persist(self, event):
        document = {name: getattr(event, name) for name in event.__dataclass_fields__}
        document['actor'] = {'kind': event.actor.kind, 'id': event.actor.id}
        self._db.execute('INSERT INTO events(document) VALUES (?)', (canonical(document),))

    def __iter__(self):
        with self._lock:
            if not self._depth:
                self._load()
            return iter(tuple(self._events))

    def __len__(self):
        with self._lock:
            if not self._depth:
                self._load()
            return len(self._events)

    def head_hash(self):
        with self._lock:
            if not self._depth:
                self._load()
            return super().head_hash()

    def close(self):
        with self._lock:
            self._db.close()
