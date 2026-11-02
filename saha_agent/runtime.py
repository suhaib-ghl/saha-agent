import fcntl
import hashlib
import json
from pathlib import Path
import threading
import time

from . import engines
from .events import normalize, select_responsibility
from .store import Store
from .connections import Connections


class Runtime:
    def __init__(self, root, config, runner=engines.run):
        self.root, self.config = Path(root).resolve(), config
        self.store = Store(self.root)
        self.runner = runner
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.thread = None
        self.lockfile = None
        self.worker_error = None
        self.connections = Connections(self)
        from .slack_socket import SlackSocket
        self.slack = SlackSocket(self)

    def start(self):
        self.lockfile = (self.root / 'runtime.lock').open('a')
        try:
            fcntl.flock(self.lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lockfile.close()
            raise RuntimeError('Another Saha runtime already owns this state directory')
        self.store.recover()
        self.thread = threading.Thread(target=self.loop, name='saha-worker', daemon=True)
        self.thread.start()
        self.wake.set()
        self.connections.start()
        try:self.slack.start()
        except Exception as e:self.connections.record('slack','error',type(e).__name__+': Slack setup failed; verify installed app credentials')

    def close(self):
        self.stop.set()
        self.slack.close()
        self.wake.set()
        if self.connections.thread:
            self.connections.thread.join(timeout=50)
            if self.connections.thread.is_alive():
                raise RuntimeError('Connection monitor did not stop; keep runtime lock held')
        if self.thread:
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                raise RuntimeError('Worker did not stop; keep runtime lock held')
        if self.lockfile and not self.lockfile.closed:
            fcntl.flock(self.lockfile, fcntl.LOCK_UN)
            self.lockfile.close()

    def ingest(self, source, payload, headers=None, engine=None):
        event = normalize(source, payload, headers)
        if event is None:
            return {"ignored": True, "reason": "Event type is outside the selected responsibility"}
        rule = select_responsibility(self.config, event)
        if not rule:
            return {"ignored": True, "reason": "No enabled responsibility matches this event"}
        engine = engine or self.config['engine']
        if engine not in {'codex', 'claude', 'demo', 'team', 'desktop'}:
            raise ValueError('Unsupported engine')
        result = self.store.ingest(event, rule, engine)
        self.wake.set()
        return result

    def ingest_snapshot(self, source, value):
        """Only called by authenticated API adapters; never trust webhook ownership."""
        from .store import digest
        rule=next((r for r in self.config['responsibilities'] if r['source']==source and r['enabled']),None)
        if not rule:return {'ignored':True}
        if source=='clickup':
            value=self.connections.clickup('/task/'+value['id'])
            if str(rule['assignee_id']) not in {str(a['id']) for a in value.get('assignees',[])}:return {'ignored':True}
            status=value.get('status',{}).get('status','').lower()
            if status in {'closed','complete','completed','qa complete','ready for deploy'}:return {'ignored':True}
            correlation=value['id'];title=value['name']
        else:
            if value['repository'] not in rule.get('repositories',[]):return {'ignored':True}
            correlation=f"{value['repository']}:{value['pr']['number']}";title=f"Review {correlation}"
        event={'source':source,'id':digest(value),'correlation':correlation,'title':title,
               'text':json.dumps(value), 'context':{'verified_api_snapshot':True}}
        result=self.store.ingest(event,rule,self.config.get('intake_engine','codex'))
        self.wake.set()
        return result

    def cancelled(self, tid):
        if self.stop.is_set() or self.store.paused():
            return True
        with self.store.connect() as db:
            row = db.execute('SELECT state FROM tasks WHERE id=?', (tid,)).fetchone()
            return not row or row[0] != 'running'

    def artifacts(self, task):
        work = self.root / 'workspaces' / task['id']
        files = []
        if work.exists():
            for path in sorted(work.rglob('*')):
                if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(work.resolve()):
                    continue
                if path.stat().st_size > 2_000_000:
                    continue
                files.append({"path": str(path.relative_to(work)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size})
                if len(files) >= 100:
                    break
        return files

    def loop(self):
        while not self.stop.is_set():
            self.wake.clear()
            try:
                task = self.store.claim(self.config['timeout_seconds'], self.config['max_runs_per_day'])
                if not task:
                    # This checks local queue/UTC budget rollover only. No API or model polling.
                    self.wake.wait(30)
                    continue
                if task['attempts'] > self.config['max_attempts']:
                    self.store.finish(task, {"summary": "Attempt budget exhausted; inspect receipts before retrying."}, 'blocked')
                    continue
                try:
                    worker_config = {**self.config, '_tool_approval': self.store.await_tool_approval}
                    result = self.runner(task, self.root, worker_config, lambda: self.cancelled(task['id']))
                    result['artifacts'] = self.artifacts(task)
                    state = {'ready': 'ready_for_review', 'needs_input': 'waiting_approval', 'blocked': 'blocked'}[result['status']]
                    # A model's success claim is not a completion proof.
                    self.store.finish(task, result, state)
                except Exception as e:
                    self.store.finish(task, {"summary": str(e), "artifacts": self.artifacts(task)}, 'failed')
            except Exception as e:
                self.worker_error = str(e)
                self.store.pause(True)
                self.wake.wait(1)

    def status(self):
        value = self.store.snapshot()
        value.update(engine=self.config['engine'], worker_error=self.worker_error,
                     worker_alive=bool(self.thread and self.thread.is_alive()),
                     limits={k: self.config[k] for k in ('max_runs_per_day', 'max_attempts', 'timeout_seconds')},
                     responsibilities=[{k: r[k] for k in ('id', 'source', 'enabled')} for r in self.config['responsibilities']])
        return value
