"""Independent conventional baseline: SQLite tables plus explicit authorization.

Evaluation fixture only. No TENET imports, event sourcing, or hash chain.
The database is authoritative for task claims and pending tool attempts.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sqlite3
from threading import RLock


def snapshot(value):
    return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))


def binding(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class Conventional:
    def __init__(self, root, *, database=':memory:', clock):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.lock = RLock()
        self.db = sqlite3.connect(str(database), check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY, grant_id TEXT UNIQUE, grant_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY, task_id TEXT, proposal_json TEXT,
                status TEXT NOT NULL);
        ''')

    def execute(self, proposal, tool_grant):
        if proposal['tool'] != tool_grant['tool']:
            raise ValueError('tool/grant mismatch')
        constraints = tool_grant.get('constraints', {})
        if set(constraints) - {'path_prefix'}:
            raise ValueError('unknown constraint')
        args = proposal['args']
        path = args.get('path')
        if not isinstance(path, str) or not path or '\x00' in path:
            raise ValueError('invalid path')
        parts = tuple(p for p in PurePosixPath(path).parts if p not in ('/', '.'))
        if not parts or '..' in parts:
            raise ValueError('invalid path')
        prefix = constraints.get('path_prefix', '/')
        if not isinstance(prefix, str) or not prefix:
            raise ValueError('invalid prefix')
        prefix_parts = tuple(p for p in PurePosixPath(prefix).parts if p not in ('/', '.'))
        if '..' in prefix_parts or parts[:len(prefix_parts)] != prefix_parts:
            raise ValueError('outside prefix')
        target = self.root
        for component in parts:
            target /= component
            if target.is_symlink():
                raise ValueError('symlink prohibited')
        target = target.resolve()
        if not target.is_relative_to(self.root):
            raise ValueError('outside root')
        if proposal['tool'] == 'fs.write':
            content = args.get('content')
            if not isinstance(content, str) or target.is_dir():
                raise ValueError('invalid write')
            encoded = content.encode('utf-8')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(encoded)
            return {'bytes_written': len(encoded)}
        if proposal['tool'] == 'fs.read':
            return {'content': target.read_text('utf-8')}
        if proposal['tool'] == 'fs.delete':
            target.unlink()
            return {'deleted': True}
        raise ValueError('unknown tool')

    def run(self, grant, proposals, *, approve=None, retrieved_ids=(), effect=None):
        grant = snapshot(grant)
        with self.lock, self.db:
            self.db.execute('INSERT INTO tasks VALUES (?,?,?)',
                            (grant['task_id'], grant['grant_id'], json.dumps(grant)))
        outcomes = []
        for value in proposals:
            proposal = snapshot(value)
            tool = next((t for t in grant['tools'] if t['tool'] == proposal['tool']), None)
            with self.lock:
                used = self.db.execute('SELECT COUNT(*) FROM attempts WHERE task_id=?',
                                       (grant['task_id'],)).fetchone()[0]
            if (self.clock() >= grant['expiry'] or used >= grant['budget'] or tool is None
                    or set(proposal.get('citations', [])) - set(retrieved_ids)
                    or proposal['tool'] not in {'fs.read', 'fs.write', 'fs.delete'}):
                outcomes.append('blocked'); continue
            # Shared fixture policy: in-grant read/write allow, delete escalates.
            request = {'proposal': proposal, 'grant': grant, 'policy': 'fixture/v1',
                       'attempt_index': len(outcomes)}
            request_binding = binding(request)
            if proposal['tool'] == 'fs.delete':
                answer = approve(request_binding) if approve else (False, '')
                if not answer[0] or answer[1] != request_binding:
                    outcomes.append('blocked'); continue
            if self.clock() >= grant['expiry']:
                outcomes.append('blocked'); continue
            with self.lock, self.db:
                cursor = self.db.execute(
                    'INSERT INTO attempts(task_id,proposal_json,status) VALUES (?,?,?)',
                    (grant['task_id'], json.dumps(proposal), 'pending'))
                operation = cursor.lastrowid
            try:
                (effect or self.execute)(proposal, tool)
                status = 'executed'
            except ValueError:
                status = 'failed'
            except Exception:
                status = 'indeterminate'
            with self.lock, self.db:
                self.db.execute('UPDATE attempts SET status=? WHERE id=?', (status, operation))
            outcomes.append(status)
            if status == 'indeterminate': break
        return outcomes

    def indeterminate(self):
        return [row[0] for row in self.db.execute(
            "SELECT id FROM attempts WHERE status IN ('pending','indeterminate')")]

    def close(self):
        self.db.close()
