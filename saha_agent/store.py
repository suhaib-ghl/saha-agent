"""Durable inbox and task transitions. One process owns execution; SQLite owns state."""
import contextlib
import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from pathlib import Path


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / "ledger.sqlite3"
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS events (
                    source TEXT, event_id TEXT, body TEXT NOT NULL, received REAL NOT NULL,
                    task_id TEXT, PRIMARY KEY(source, event_id));
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, correlation TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL, responsibility TEXT NOT NULL, engine TEXT NOT NULL,
                    state TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                    claimed_revision INTEGER, lease TEXT, lease_until REAL,
                    attempts INTEGER NOT NULL DEFAULT 0, result TEXT,
                    created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    question TEXT NOT NULL, scope_hash TEXT NOT NULL,
                    expires REAL NOT NULL, decision TEXT, note TEXT, decided REAL);
                CREATE TABLE IF NOT EXISTS receipts (
                    id INTEGER PRIMARY KEY, task_id TEXT, kind TEXT, body TEXT, at REAL);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, scope TEXT, text TEXT, created REAL, active INTEGER);
                CREATE TABLE IF NOT EXISTS feedback (id TEXT PRIMARY KEY, task_id TEXT, text TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS tool_approvals (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    lease TEXT NOT NULL, request TEXT NOT NULL, scope_hash TEXT NOT NULL,
                    expires REAL NOT NULL, decision TEXT, decided REAL);
                INSERT OR IGNORE INTO settings VALUES ('paused', 'false');
            ''')
        self.path.chmod(0o600)

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    @contextlib.contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    @staticmethod
    def receipt(db, task_id, kind, body):
        db.execute("INSERT INTO receipts(task_id,kind,body,at) VALUES(?,?,?,?)",
                   (task_id, kind, encode(body), time.time()))

    def ingest(self, event, responsibility, engine):
        """Deduplication and enqueue commit together, before the receiver acknowledges."""
        now = time.time()
        correlation = f"{event['source']}:{responsibility['id']}:{event['correlation']}"
        with self.transaction() as db:
            previous = db.execute("SELECT task_id,body FROM events WHERE source=? AND event_id=?",
                                  (event['source'], event['id'])).fetchone()
            if previous:
                if previous['body'] != encode(event):
                    raise Conflict("Event ID reused with different content")
                return {"task_id": previous['task_id'], "duplicate": True}
            row = db.execute("SELECT * FROM tasks WHERE correlation=?", (correlation,)).fetchone()
            if row:
                tid = row['id']
                state = row['state'] if row['state'] in ('running', 'cancelled') else 'queued'
                db.execute("UPDATE tasks SET revision=revision+1,state=?,updated=?,attempts=0 WHERE id=?",
                           (state, now, tid))
                db.execute("UPDATE approvals SET decision='superseded',decided=? WHERE task_id=? AND decision IS NULL",
                           (now, tid))
            else:
                tid = uuid.uuid4().hex
                db.execute("INSERT INTO tasks(id,correlation,title,responsibility,engine,state,created,updated) VALUES(?,?,?,?,?,'queued',?,?)",
                           (tid, correlation, event['title'][:200], encode(responsibility), engine, now, now))
            db.execute("INSERT INTO events VALUES(?,?,?,?,?)",
                       (event['source'], event['id'], encode(event), now, tid))
            self.receipt(db, tid, 'event_received', {"source": event['source'], "event_id": event['id']})
            return {"task_id": tid, "duplicate": False}

    def paused(self):
        with self.connect() as db:
            return db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0] == 'true'

    def setting(self, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.transaction() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, encode(value)))

    def remember(self, scope, text):
        if scope not in {'all','local','clickup','github','slack'} or not isinstance(text,str) or not 0 < len(text.strip()) <= 2000:
            raise ValueError('Choose a valid scope and an instruction of 1–2000 characters')
        value = {'id':uuid.uuid4().hex, 'scope':scope, 'text':text.strip(), 'created':time.time(), 'source':'authenticated_owner'}
        with self.transaction() as db:
            if db.execute('SELECT COUNT(*) FROM memories WHERE active=1').fetchone()[0] >= 50:
                raise Conflict('Memory limit reached; retire obsolete instructions first')
            db.execute('INSERT INTO memories VALUES (?,?,?,?,1)', (value['id'],scope,value['text'],value['created']))
            self.receipt(db,None,'owner_memory_saved',value)
        return value

    def memories(self, scope=None):
        with self.connect() as db:
            rows = db.execute('SELECT id,scope,text,created FROM memories WHERE active=1 ORDER BY created').fetchall()
            return [dict(r) for r in rows if scope is None or r['scope'] in {'all',scope}]

    def forget(self, mid):
        with self.transaction() as db:
            db.execute('UPDATE memories SET active=0 WHERE id=?',(mid,))
            self.receipt(db,None,'owner_memory_retired',{'id':mid})

    def feedback(self, tid, text):
        if not isinstance(text,str) or not 0 < len(text.strip()) <= 8000:
            raise ValueError('Feedback must contain 1–8000 characters')
        with self.transaction() as db:
            task=db.execute('SELECT state FROM tasks WHERE id=?',(tid,)).fetchone()
            if not task or task['state']=='cancelled': raise Conflict('Unknown or cancelled task')
            db.execute('INSERT INTO feedback VALUES (?,?,?,?)',(uuid.uuid4().hex,tid,text.strip(),time.time()))
            db.execute("UPDATE tasks SET state='queued', revision=revision+1, attempts=0, lease=NULL,lease_until=NULL,updated=? WHERE id=?",(time.time(),tid))
            db.execute("UPDATE approvals SET decision='superseded',decided=? WHERE task_id=? AND decision IS NULL",(time.time(),tid))
            self.receipt(db,tid,'owner_feedback',{'text':text.strip()})

    def pause(self, paused):
        with self.transaction() as db:
            db.execute("UPDATE settings SET value=? WHERE key='paused'", ('true' if paused else 'false',))
            self.receipt(db, None, 'paused' if paused else 'resumed', {})

    def recover(self):
        """Only call while holding the runtime OS lock; do not silently replay uncertain work."""
        with self.transaction() as db:
            db.execute("UPDATE tool_approvals SET decision='cancel',decided=? WHERE decision IS NULL", (time.time(),))
            rows = db.execute("SELECT id FROM tasks WHERE state='running'").fetchall()
            for row in rows:
                db.execute("UPDATE tasks SET state='interrupted',lease=NULL,lease_until=NULL,updated=? WHERE id=?",
                           (time.time(), row['id']))
                self.receipt(db, row['id'], 'interrupted', {"reason": "Runtime restarted; inspect workspace before retry"})
            return len(rows)

    def claim(self, timeout, daily_limit):
        now = time.time()
        midnight = now - now % 86400
        with self.transaction() as db:
            if db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0] == 'true':
                return None
            if db.execute("SELECT 1 FROM tasks WHERE state='running'").fetchone():
                return None
            used = db.execute("SELECT COUNT(*) FROM receipts WHERE kind='run_started' AND at>=?", (midnight,)).fetchone()[0]
            if used >= daily_limit:
                return None
            row = db.execute("SELECT * FROM tasks WHERE state='queued' ORDER BY updated,id LIMIT 1").fetchone()
            if not row:
                return None
            token = secrets.token_urlsafe(24)
            db.execute("UPDATE tasks SET state='running',claimed_revision=revision,lease=?,lease_until=?,attempts=attempts+1,updated=? WHERE id=?",
                       (token, now + timeout + 60, now, row['id']))
            self.receipt(db, row['id'], 'run_started', {"revision": row['revision'], "engine": row['engine']})
            task = dict(db.execute("SELECT * FROM tasks WHERE id=?", (row['id'],)).fetchone())
            task['events'] = [json.loads(x[0]) for x in db.execute("SELECT body FROM events WHERE task_id=? ORDER BY received", (row['id'],))]
            task['decisions'] = [dict(x) for x in db.execute("SELECT question,decision,note,revision FROM approvals WHERE task_id=? AND decided IS NOT NULL", (row['id'],))]
            task['owner_feedback'] = [dict(x) for x in db.execute('SELECT text,created FROM feedback WHERE task_id=? ORDER BY created',(row['id'],))]
            task['memories'] = self.memories(json.loads(task['responsibility'])['source'])
            return task

    def finish(self, task, result, state):
        if state not in {'ready_for_review', 'completed', 'waiting_approval', 'failed', 'blocked', 'queued'}:
            raise ValueError('Invalid finish state')
        with self.transaction() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task['id'],)).fetchone()
            if not row or row['state'] != 'running' or row['lease'] != task['lease']:
                return False  # cancelled or superseded execution cannot commit
            if row['lease_until'] < time.time():
                # The runner has returned (and terminated its child) before this
                # call. Preserve uncertain work without leaving a running row
                # that permanently blocks the single-worker queue after sleep.
                interrupted = {'summary': 'Execution lease expired; outcome unverified. Inspect saved artifacts before an explicit retry.',
                               'artifacts': result.get('artifacts', [])}
                db.execute("UPDATE tasks SET state='interrupted',result=?,lease=NULL,lease_until=NULL,updated=? WHERE id=?",
                           (encode(interrupted), time.time(), task['id']))
                self.receipt(db, task['id'], 'interrupted', {'reason': 'Execution lease expired', 'revision': task['claimed_revision']})
                return False
            if row['revision'] != task['claimed_revision']:
                state = 'queued'
            if state == 'waiting_approval':
                question = result.get('question', '').strip()
                if not question:
                    raise ValueError('Approval requires a concrete question')
                aid = uuid.uuid4().hex
                scope = digest({"task_id": task['id'], "revision": row['revision'], "question": question})
                db.execute("INSERT INTO approvals(id,task_id,revision,question,scope_hash,expires) VALUES(?,?,?,?,?,?)",
                           (aid, task['id'], row['revision'], question, scope, time.time() + 86400))
            db.execute("UPDATE tasks SET state=?,result=?,lease=NULL,lease_until=NULL,updated=? WHERE id=?",
                       (state, encode(result), time.time(), task['id']))
            self.receipt(db, task['id'], 'run_finished', {"revision": task['claimed_revision'], "state": state, "result": result})
            return True

    def decide(self, aid, scope, decision, note):
        if decision not in {'approve', 'deny'} or not isinstance(note, str) or len(note) > 8000:
            raise ValueError('Invalid owner decision')
        with self.transaction() as db:
            a = db.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
            if not a:
                raise Conflict('Unknown approval')
            t = db.execute("SELECT * FROM tasks WHERE id=?", (a['task_id'],)).fetchone()
            if a['decision'] or a['expires'] < time.time() or a['revision'] != t['revision'] or t['state'] != 'waiting_approval' or not secrets.compare_digest(a['scope_hash'], scope):
                raise Conflict('Stale, expired, or already answered approval')
            db.execute("UPDATE approvals SET decision=?,note=?,decided=? WHERE id=?", (decision, note, time.time(), aid))
            db.execute("UPDATE tasks SET state=?,attempts=0,updated=? WHERE id=?",
                       ('queued' if decision == 'approve' else 'cancelled', time.time(), t['id']))
            self.receipt(db, t['id'], 'owner_decision', {"approval_id": aid, "decision": decision, "scope_hash": scope})
            return t['id']

    def request_tool_approval(self, task, request, expires):
        """A live tool request, never a model-generated question or permission grant."""
        ident = uuid.uuid4().hex
        scope = digest({'task': task['id'], 'revision': task['claimed_revision'],
                        'lease': task['lease'], 'request': request, 'id': ident})
        with self.transaction() as db:
            t = db.execute('SELECT * FROM tasks WHERE id=?', (task['id'],)).fetchone()
            if not self._live_tool_task(t, task['claimed_revision'], task['lease']):
                raise Conflict('Tool request belongs to an inactive execution')
            db.execute('INSERT INTO tool_approvals VALUES (?,?,?,?,?,?,?,NULL,NULL)',
                       (ident, task['id'], task['claimed_revision'], task['lease'], encode(request), scope,
                        min(expires, t['lease_until'])))
            self.receipt(db, task['id'], 'tool_approval_requested', {'id': ident, 'scope_hash': scope, 'request': request})
        return ident

    @staticmethod
    def _live_tool_task(task, revision, lease):
        return (task and task['state'] == 'running' and task['revision'] == revision
                and task['lease'] == lease and task['lease_until'] > time.time())

    def tool_decision(self, ident, scope, decision):
        if decision not in {'accept', 'decline'}:
            raise ValueError('Expected accept or decline')
        with self.transaction() as db:
            a = db.execute('SELECT * FROM tool_approvals WHERE id=?', (ident,)).fetchone()
            if not a:
                raise Conflict('Unknown live tool request')
            t = db.execute('SELECT * FROM tasks WHERE id=?', (a['task_id'],)).fetchone()
            if (a['decision'] or a['expires'] <= time.time() or self.paused()
                    or not self._live_tool_task(t, a['revision'], a['lease'])
                    or not secrets.compare_digest(a['scope_hash'], scope)):
                raise Conflict('Tool request is stale, expired, paused, or already answered')
            db.execute('UPDATE tool_approvals SET decision=?,decided=? WHERE id=?', (decision, time.time(), ident))
            self.receipt(db, a['task_id'], 'tool_approval_answered', {'id': ident, 'decision': decision, 'scope_hash': scope})

    def await_tool_approval(self, task, request, expires, cancelled):
        ident = self.request_tool_approval(task, request, expires)
        try:
            while time.time() < expires and not cancelled():
                with self.connect() as db:
                    a = db.execute('SELECT * FROM tool_approvals WHERE id=?', (ident,)).fetchone()
                    t = db.execute('SELECT * FROM tasks WHERE id=?', (task['id'],)).fetchone()
                if not self._live_tool_task(t, a['revision'], a['lease']) or a['expires'] <= time.time():
                    break
                if a['decision']:
                    return a['decision'] if a['decision'] in {'accept', 'decline'} else 'cancel'
                time.sleep(.2)
            return 'cancel'
        finally:
            with self.transaction() as db:
                db.execute("UPDATE tool_approvals SET decision='cancel',decided=? WHERE id=? AND decision IS NULL", (time.time(), ident))

    def control_task(self, tid, action, engine=None):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
            if not row:
                raise Conflict('Unknown task')
            if action == 'cancel':
                state = 'cancelled'
            elif action == 'retry' and row['state'] in {'failed', 'blocked', 'interrupted'}:
                state = 'queued'
            elif action == 'complete' and row['state'] == 'ready_for_review':
                state = 'completed'
            else:
                raise Conflict('Task cannot make that transition')
            if engine and engine not in {'codex', 'claude', 'demo', 'team', 'desktop'}:
                raise ValueError('Unknown engine')
            db.execute("UPDATE tasks SET state=?,engine=?,attempts=?,lease=NULL,lease_until=NULL,updated=? WHERE id=?",
                       (state, engine or row['engine'], 0 if action == 'retry' else row['attempts'], time.time(), tid))
            db.execute("UPDATE approvals SET decision='cancelled',decided=? WHERE task_id=? AND decision IS NULL", (time.time(), tid))
            self.receipt(db, tid, action, {"engine": engine or row['engine']})

    def snapshot(self):
        with self.connect() as db:
            tasks = []
            for row in db.execute("SELECT * FROM tasks ORDER BY updated DESC LIMIT 100"):
                t = dict(row)
                for key in ('lease', 'lease_until', 'responsibility'):
                    t.pop(key, None)
                t['result'] = json.loads(t['result']) if t['result'] else None
                tasks.append(t)
            approvals = [dict(r) for r in db.execute("SELECT * FROM approvals WHERE decision IS NULL ORDER BY expires")]
            tool_approvals = []
            for row in db.execute('''SELECT a.* FROM tool_approvals a JOIN tasks t ON t.id=a.task_id
                    WHERE a.decision IS NULL AND a.expires>? AND t.state='running'
                    AND t.revision=a.revision AND t.lease=a.lease AND t.lease_until>?''', (time.time(), time.time())):
                a = dict(row); a.pop('lease'); a['request'] = json.loads(a['request'])
                tool_approvals.append(a)
            receipts = [dict(r) for r in db.execute("SELECT * FROM receipts ORDER BY id DESC LIMIT 50")]
            for r in receipts:
                r['body'] = json.loads(r['body'])
            return {"tasks": tasks, "approvals": approvals, "tool_approvals": tool_approvals, "receipts": receipts,
                    "paused": self.paused(), "events": db.execute('SELECT COUNT(*) FROM events').fetchone()[0]}
