"""Explicit API integrations. Credentials stay in existing local login stores.

Polling checks metadata, never calls a model on an unchanged inbox, and never
replays the historical backlog on first connection. Webhook ingress is separate.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import time
import urllib.request


class Connections:
    def __init__(self, runtime):
        self.runtime = runtime
        self.config = runtime.config.get('integrations', {})
        self.state = {}
        self.lock = threading.Lock()
        self.thread = None

    def record(self, name, status, detail, **extra):
        with self.lock:
            self.state[name] = {'status':status,'detail':detail,'checked_at':time.time(),**extra}

    def snapshot(self):
        with self.lock: result = {k:dict(v) for k,v in self.state.items()}
        for name in ('slack','clickup','github','computer'):
            result.setdefault(name, {'status':'not_configured','detail':'No verified adapter configured'})
        for value in result.values():
            if value.get('checked_at') and time.time()-value['checked_at'] > 180:
                value['status']='stale'
        return result

    def clickup(self, path):
        cfg=self.config.get('clickup',{})
        token=None
        if cfg.get('token_file'):
            token=Path(cfg['token_file']).expanduser().read_text().strip()
        elif cfg.get('credential_config') and cfg.get('credential_server'):
            # Explicit legacy opt-in only; never scan another tool's login files.
            location=Path(cfg['credential_config']).expanduser()
            server=json.loads(location.read_text())['mcpServers'][cfg['credential_server']]
            token=server.get('headers',{}).get('X-ClickUp-Token')
        if not token: raise RuntimeError('Configure a private ClickUp token_file before enabling this integration')
        if not path.startswith('/') or '..' in path or '?' in path.split('/')[1]: raise ValueError('Invalid API path')
        req=urllib.request.Request('https://api.clickup.com/api/v2'+path,headers={'Authorization':token})
        with urllib.request.urlopen(req,timeout=15) as response:
            return json.load(response)

    def gh(self, path):
        p=subprocess.run(['gh','api',path],capture_output=True,text=True,timeout=20)
        if p.returncode: raise RuntimeError('GitHub CLI request failed; inspect authenticated gh access')
        return json.loads(p.stdout)

    def accept_tasks(self, tasks):
        previous=self.runtime.store.setting('clickup_revisions',None)
        current=dict(previous or {})
        owner=str(self.config.get('clickup',{}).get('user_id',''))
        for t in tasks:
            status=t.get('status',{})
            status=status.get('status','') if isinstance(status,dict) else status
            rev=str(t.get('date_updated',''))
            eligible = status.lower() not in {'closed','complete','completed','qa complete','ready for deploy'}
            owned=owner in {str(a['id']) for a in t.get('assignees',[])}
            if previous is not None and current.get(t['id']) != rev and eligible and owned:
                # Fetch complete authoritative context before enqueueing; a failed
                # fetch/ingestion leaves the revision eligible for retry.
                self.runtime.ingest_snapshot('clickup',t)
            current[t['id']]=rev
        self.runtime.store.set_setting('clickup_revisions',current)

    def scan_clickup(self):
        cfg=self.config['clickup']
        user=self.clickup('/user')['user']
        if str(user['id']) != str(cfg['user_id']): raise RuntimeError('ClickUp identity differs from configured owner')
        tasks=[]
        for page in range(100):
            data=self.clickup(f"/team/{cfg['workspace_id']}/task?assignees%5B%5D={cfg['user_id']}&include_closed=false&page={page}&order_by=updated")
            batch=data.get('tasks',[]);tasks.extend(batch)
            if data.get('last_page') is True or len(batch)<100: break
        else: raise RuntimeError('ClickUp pagination exceeded limit; cursor not advanced')
        self.accept_tasks(tasks)
        self.record('clickup','connected','Authenticated API; watches assignment changes every 30 seconds',identity=user['id'],event_transport='polling',task_count=len(tasks))

    def scan_github(self):
        user=self.gh('user')
        cfg=self.config['github']
        if user['login'] != cfg['login']: raise RuntimeError('GitHub identity differs from configured owner')
        previous=self.runtime.store.setting('github_revisions_v2',None)
        current=dict(previous or {})
        for watched in cfg.get('pull_requests',[]):
            repo,number=watched['repository'],int(watched['number'])
            pr=self.gh(f'repos/{repo}/pulls/{number}')
            checks=self.gh(f"repos/{repo}/commits/{pr['head']['sha']}/check-runs?per_page=100")
            key=f'{repo}#{number}'
            snapshot={**{k:pr[k] for k in ('updated_at','state','head')},'checks':checks}
            rev=hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()
            if previous is not None and previous.get(key)!=rev:
                self.runtime.ingest_snapshot('github',{'repository':repo,'pr':pr,'checks':checks,'revision':rev})
            current[key]=rev
        self.runtime.store.set_setting('github_revisions_v2',current)
        self.record('github','connected','Authenticated gh; watches selected PRs every 30 seconds',identity=user['login'],event_transport='polling')

    def scan_computer(self):
        from .desktop import probe
        value=probe(self.config['computer']['manifest'])
        with self.runtime.store.connect() as db:
            row=db.execute("SELECT state,result FROM tasks WHERE engine='desktop' ORDER BY updated DESC LIMIT 1").fetchone()
        if row and row['state'] in {'waiting_approval','blocked','failed'} and row['result']:
            result=json.loads(row['result'])
            value['status']='needs_attention'
            value['detail']='Native service responds, but the latest app job could not complete: '+result.get('summary','Inspect its decision or execution receipt.')[:200]
        elif row and row['state']=='running':
            value['detail']='Native app job is running. Any live permission request appears under Needs your decision.'
        elif row and row['state']=='completed' and row['result']:
            result=json.loads(row['result'])
            headline=result.get('summary','Native app task completed and reviewed').split('. ')[0][:300].rstrip('.')
            value['detail']=headline+'. New worker sessions may need permission. Browser access depends on an authorized host.'
        self.record('computer',value['status'],value['detail'],native_apps=value.get('native_apps',False),browser=value.get('browser',False))

    def scan(self):
        for name, method in [('clickup',self.scan_clickup),('github',self.scan_github)]:
            if not self.config.get(name,{}).get('enabled'):continue
            try:method()
            except Exception as e:self.record(name,'error',f'{type(e).__name__}: '+str(e)[:180])
        if self.config.get('computer',{}).get('enabled'):
            try:self.scan_computer()
            except Exception as e:self.record('computer','error',f'{type(e).__name__}: '+str(e)[:180])
        if not self.config.get('slack',{}).get('enabled'):
            self.record('slack','needs_setup','Push events need a configured Slack app. This listener is separate from native Slack app access.',event_transport='not_connected')
        elif self.runtime.slack.client:
            connected=self.runtime.slack.client.is_connected()
            self.record('slack','connected' if connected else 'reconnecting','Socket Mode transport',event_transport='websocket')

    def loop(self):
        while not self.runtime.stop.is_set():
            self.scan()
            self.runtime.stop.wait(30)

    def start(self):
        if not self.config:return
        self.thread=threading.Thread(target=self.loop,name='saha-connections',daemon=True)
        self.thread.start()
