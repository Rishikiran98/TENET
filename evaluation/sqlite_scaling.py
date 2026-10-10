"""Append scaling with fixed payloads and SQLite FULL durability; one run per size."""
import argparse
import json
import platform
import sqlite3
import tempfile
from pathlib import Path
from time import perf_counter
from tenet.events import SQLiteEventLog, Actor, taxonomy as T


def measure(size):
    with tempfile.TemporaryDirectory() as folder:
        log = SQLiteEventLog(Path(folder)/'events.db')
        start = perf_counter()
        for _ in range(size):
            log.append(namespace='n', actor=Actor('user','fixture'), event_type=T.TASK_COMPLETED,
                       payload={'summary':'fixed payload'}, correlation_id='task')
        duration = perf_counter() - start
        assert len(log) == size and log.verify()
        log.close()
        return {'appends':size, 'seconds':round(duration, 6)}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--label', required=True)
    args = parser.parse_args()
    print(json.dumps({'label':args.label, 'python':platform.python_version(),
                      'sqlite':sqlite3.sqlite_version, 'journal':'WAL', 'synchronous':'FULL',
                      'runs_per_size':1, 'measurements':[measure(n) for n in (500,1000,2000)]}, indent=2))
