"""Independent ordinary-table controller for the A3 bounded workload.

No TENET imports or shared enforcement. Uses the same external task contract.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
from uuid import uuid4


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def sha(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


@contextmanager
def transaction(db):
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.execute('COMMIT')
    except BaseException:
        if db.in_transaction: db.execute('ROLLBACK')
        raise


def connect(path):
    db = sqlite3.connect(str(path), isolation_level=None)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    return db


def validate(p, capability):
    tool, args = p['tool'], p['args']
    if tool != capability['tool']: raise ValueError('tool mismatch')
    constraints = capability.get('constraints', {})
    if tool == 'journal.append':
        if set(args) != {'journal','content'} or set(constraints) != {'journal'}:
            raise ValueError('invalid journal shape')
        if (not isinstance(args['journal'], str) or
            re.fullmatch(r'[A-Za-z0-9_-]{1,64}', args['journal']) is None or
            args['journal'] != constraints['journal']): raise ValueError('journal denied')
        if not isinstance(args['content'], str) or len(args['content'].encode('utf-8')) > 4096:
            raise ValueError('invalid content')
        return
    if tool not in {'fs.read','fs.write'}: raise ValueError('unsupported tool')
    if set(args) != ({'path','content'} if tool == 'fs.write' else {'path'}):
        raise ValueError('invalid arguments')
    if set(constraints) - {'path_prefix'}: raise ValueError('unknown constraint')
    def parts(value, root=False):
        if not isinstance(value,str) or not value or any(c in value for c in ('\\',':','\x00')) or value.startswith('//'):
            raise ValueError('invalid path')
        result = tuple(v for v in PurePosixPath(value).parts if v not in ('/','.'))
        if '..' in result or any(v.endswith((' ','.')) for v in result) or not result and not root:
            raise ValueError('invalid components')
        return result
    path = parts(args['path']); prefix = parts(constraints.get('path_prefix','/'), True)
    if path[:len(prefix)] != prefix: raise ValueError('outside grant')
    if tool == 'fs.write':
        if not isinstance(args['content'],str): raise ValueError('invalid content')
        args['content'].encode('utf-8')


class ConventionalRecovery:
    def __init__(self, root, *, clock, checkpoint=lambda phase: None):
        self.root, self.clock, self.checkpoint = Path(root), clock, checkpoint
        self.db = connect(self.root/'controller.db')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, grant_id TEXT UNIQUE, grant_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, task TEXT NOT NULL,
                proposal TEXT NOT NULL, binding TEXT NOT NULL, store_id TEXT NOT NULL,
                status TEXT NOT NULL, result TEXT);
            CREATE TABLE IF NOT EXISTS resolutions(operation TEXT PRIMARY KEY, state TEXT NOT NULL,
                binding TEXT NOT NULL, store_id TEXT NOT NULL);
        ''')
        self.store = connect(self.root/'reports.db')
        self.store.executescript('''
            CREATE TABLE IF NOT EXISTS identity(id TEXT PRIMARY KEY, singleton INTEGER UNIQUE CHECK(singleton=1));
            CREATE TABLE IF NOT EXISTS reports(operation TEXT PRIMARY KEY, binding TEXT NOT NULL,
                state TEXT NOT NULL, journal TEXT NOT NULL, content TEXT);
        ''')
        self.store.execute('INSERT OR IGNORE INTO identity VALUES (?,1)',(uuid4().hex,))
        self.store_id = self.store.execute('SELECT id FROM identity WHERE singleton=1').fetchone()[0]

    def settle(self, op, binding, proposal, *, cancel):
        with transaction(self.store):
            row = self.store.execute('SELECT binding,state,journal,content FROM reports WHERE operation=?',(op,)).fetchone()
            if row is None:
                state = 'cancelled' if cancel else 'applied'
                self.store.execute('INSERT INTO reports VALUES (?,?,?,?,?)',
                    (op,binding,state,proposal['args']['journal'],None if cancel else proposal['args']['content']))
            else:
                old, state, journal, content = row
                if old != binding or journal != proposal['args']['journal'] or (state == 'applied' and content != proposal['args']['content']):
                    raise ValueError('operation identity mismatch')
        return state

    def execute(self, proposal, capability):
        validate(proposal, capability)
        target = self.root
        for part in PurePosixPath(proposal['args']['path']).parts:
            if part in ('/','.'): continue
            target /= part
            if target.is_symlink(): raise ValueError('symlink prohibited')
        target = target.resolve()
        if not target.is_relative_to(self.root.resolve()): raise ValueError('outside root')
        if proposal['tool'] == 'fs.read':
            with target.open('rb') as stream: data = stream.read(4097)
            prefix = data[:4096]
            try: content = prefix.decode('utf-8')
            except UnicodeDecodeError as exc:
                if exc.reason != 'unexpected end of data' or len(data) <= 4096: raise
                content = prefix[:exc.start].decode('utf-8')
            return {'content':content, 'truncated':len(data)>len(content.encode())}
        target.write_text(proposal['args']['content'],encoding='utf-8')
        return {'bytes_written':len(proposal['args']['content'].encode())}

    def run(self, grant, brain):
        grant = json.loads(encode(grant))
        with transaction(self.db):
            self.db.execute('INSERT INTO tasks VALUES (?,?,?)',(grant['task_id'],grant['grant_id'],encode(grant)))
        observations = []
        for _ in range(20):
            proposal = brain(tuple(observations))
            if proposal is None: return observations
            proposal = json.loads(encode(proposal))
            capability = next((t for t in grant['tools'] if t['tool'] == proposal['tool']),None)
            used = self.db.execute('SELECT COUNT(*) FROM attempts WHERE task=?',(grant['task_id'],)).fetchone()[0]
            try:
                if capability is None or self.clock() >= grant['expiry'] or used >= grant['budget']:
                    raise ValueError('authority denied')
                validate(proposal, capability)
            except (ValueError, UnicodeError):
                observations.append({'tool':proposal['tool'],'status':'blocked','result':None}); continue
            # Explicit evaluation fixture allows only the three validated tools.
            # No approvals are needed by this workload; production policy is not defined.
            if self.clock() >= grant['expiry']:
                observations.append({'tool':proposal['tool'],'status':'blocked','result':None}); continue
            op = uuid4().hex
            bound = sha({'operation':op,'proposal':proposal,'grant':grant,'store':self.store_id})
            with transaction(self.db):
                self.db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?,NULL)',
                    (op,grant['task_id'],encode(proposal),bound,self.store_id,'pending'))
                if proposal['tool'] == 'journal.append': self.checkpoint('before_dispatch_commit')
            if proposal['tool'] == 'journal.append': self.checkpoint('after_dispatch_commit')
            result, status = None, 'executed'
            try:
                if proposal['tool'] == 'journal.append':
                    validate(proposal,capability)
                    state = self.settle(op,bound,proposal,cancel=False)
                    if state == 'cancelled': status = 'failed'
                    else: result = {'state':'applied'}
                else: result = self.execute(proposal,capability)
            except Exception: status = 'indeterminate'
            if proposal['tool'] == 'journal.append': self.checkpoint('after_effect')
            # Read text goes to the Brain, never into the durable outcome.
            receipt = {'content_sha256':hashlib.sha256(result['content'].encode()).hexdigest()} if proposal['tool']=='fs.read' and result else result
            with transaction(self.db):
                self.db.execute('UPDATE attempts SET status=?,result=? WHERE id=?',(status,encode(receipt),op))
            observations.append({'tool':proposal['tool'],'status':status,'result':result})
            if status == 'indeterminate': return observations
        return observations

    def pending(self):
        return [r[0] for r in self.db.execute("SELECT id FROM attempts WHERE status IN ('pending','indeterminate') AND id NOT IN (SELECT operation FROM resolutions)")]

    def reconcile(self, op):
        with transaction(self.db):
            row = self.db.execute('SELECT state,store_id FROM resolutions WHERE operation=?',(op,)).fetchone()
            if row:
                if row[1] != self.store_id: raise ValueError('wrong store')
                return row[0]
            attempt = self.db.execute('SELECT proposal,binding,store_id,status FROM attempts WHERE id=?',(op,)).fetchone()
            if not attempt or attempt[2] != self.store_id or attempt[3] not in {'pending','indeterminate'}:
                raise ValueError('not an unresolved operation in this store')
            proposal = json.loads(attempt[0])
            if proposal['tool'] != 'journal.append': raise ValueError('unsupported reconciliation')
            state = self.settle(op,attempt[1],proposal,cancel=True)
            self.checkpoint('during_reconciliation')
            self.db.execute('INSERT INTO resolutions VALUES (?,?,?,?)',(op,state,attempt[1],self.store_id))
        self.checkpoint('after_reconciliation')
        return state

    def close(self):
        self.db.close(); self.store.close()
