import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from saha_agent import engines
from saha_agent.events import Unauthorized, normalize, select_responsibility, signature, verify
from saha_agent.runtime import Runtime
from saha_agent.server import make_server
from saha_agent.store import Conflict, Store

CONFIG = json.loads((Path(__file__).parents[1] / 'config.example.json').read_text())
RULE = CONFIG['responsibilities'][0]


def event(i='one', correlation='thread'):
    return normalize('local', {'id': i, 'correlation': correlation, 'title': 'Test', 'text': 'Local deliverable'})


def wait_for(predicate, seconds=5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError('Expected state did not arrive')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)

    def queued(self):
        return self.store.ingest(event(), RULE, 'demo')['task_id']

    def approval(self):
        self.queued()
        task = self.store.claim(60, 20)
        self.store.finish(task, {'question': 'Write local note?'}, 'waiting_approval')
        return self.store.snapshot()['approvals'][0]

    def test_deduplication_and_collision(self):
        tid = self.queued()
        self.assertEqual(self.store.ingest(event(), RULE, 'demo'), {'task_id': tid, 'duplicate': True})
        altered = event(); altered['text'] = 'different'
        with self.assertRaises(Conflict):
            self.store.ingest(altered, RULE, 'demo')
        self.assertEqual(self.store.snapshot()['events'], 1)

    def test_single_claim_and_pause(self):
        self.queued()
        self.store.pause(True)
        self.assertIsNone(self.store.claim(60, 20))
        self.store.pause(False)
        self.assertIsNotNone(self.store.claim(60, 20))
        self.assertIsNone(self.store.claim(60, 20))

    def test_new_revision_requeues_active_result(self):
        self.queued(); task = self.store.claim(60, 20)
        self.store.ingest(event('two'), RULE, 'demo')
        self.store.finish(task, {'question': 'Old question'}, 'waiting_approval')
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot['tasks'][0]['state'], 'queued')
        self.assertEqual(snapshot['approvals'], [])

    def test_approval_is_scoped_and_single_use(self):
        a = self.approval()
        with self.assertRaises(Conflict):
            self.store.decide(a['id'], 'wrong', 'approve', '')
        self.store.decide(a['id'], a['scope_hash'], 'approve', 'Yes')
        with self.assertRaises(Conflict):
            self.store.decide(a['id'], a['scope_hash'], 'approve', '')
        self.assertEqual(self.store.snapshot()['tasks'][0]['state'], 'queued')

    def test_new_event_invalidates_approval(self):
        a = self.approval(); self.store.ingest(event('two'), RULE, 'demo')
        with self.assertRaises(Conflict):
            self.store.decide(a['id'], a['scope_hash'], 'approve', '')

    def test_expired_approval_rejected(self):
        a = self.approval()
        with self.store.transaction() as db:
            db.execute('UPDATE approvals SET expires=0')
        with self.assertRaises(Conflict):
            self.store.decide(a['id'], a['scope_hash'], 'approve', '')

    def test_cancel_invalidates_running_lease(self):
        tid = self.queued(); task = self.store.claim(60, 20)
        self.store.control_task(tid, 'cancel')
        self.assertFalse(self.store.finish(task, {}, 'ready_for_review'))
        self.assertEqual(self.store.snapshot()['tasks'][0]['state'], 'cancelled')

    def test_restart_preserves_pending_decision(self):
        a = self.approval(); restarted = Store(self.tmp.name)
        restarted.recover()
        self.assertEqual(restarted.snapshot()['approvals'][0]['id'], a['id'])

    def test_expired_worker_cannot_complete_or_block_next_job(self):
        tid = self.queued(); task = self.store.claim(60, 20)
        next_id = self.store.ingest(event('next', 'other-thread'), RULE, 'demo')['task_id']
        with self.store.transaction() as db:
            db.execute('UPDATE tasks SET lease_until=0 WHERE id=?', (tid,))
        self.assertFalse(self.store.finish(task, {'summary':'Unverified success'}, 'ready_for_review'))
        rows = {t['id']:t for t in self.store.snapshot()['tasks']}
        self.assertEqual(rows[tid]['state'], 'interrupted')
        self.assertEqual(self.store.snapshot()['approvals'], [])
        self.assertEqual(self.store.claim(60, 20)['id'], next_id)

    def test_uncertain_run_not_silently_replayed(self):
        tid = self.queued(); self.store.claim(60, 20)
        self.assertEqual(self.store.recover(), 1)
        self.assertIsNone(self.store.claim(60, 20))
        self.store.control_task(tid, 'retry', 'claude')
        task = self.store.claim(60, 20)
        self.assertEqual((task['engine'], task['attempts']), ('claude', 1))

    def test_daily_run_cap(self):
        self.queued(); task = self.store.claim(60, 1)
        self.store.finish(task, {}, 'failed')
        self.store.control_task(task['id'], 'retry')
        self.assertIsNone(self.store.claim(60, 1))

    def test_denial_cancels(self):
        a = self.approval(); self.store.decide(a['id'], a['scope_hash'], 'deny', '')
        self.assertEqual(self.store.snapshot()['tasks'][0]['state'], 'cancelled')


class EventTests(unittest.TestCase):
    def test_slack_signature_and_staleness(self):
        body = b'{"type":"event_callback"}'
        headers = {'X-Slack-Request-Timestamp': '1000', 'X-Slack-Signature': 'v0=' + signature('secret', b'v0:1000:' + body)}
        verify('slack', headers, body, 'secret', now=1001)
        for value, now in ((body+b' ', 1001), (body, 1400)):
            with self.assertRaises(Unauthorized):
                verify('slack', headers, value, 'secret', now=now)

    def test_github_and_clickup_signatures(self):
        for source, header, prefix in [('github', 'X-Hub-Signature-256', 'sha256='), ('clickup', 'X-Signature', '')]:
            verify(source, {header: prefix+signature('secret', b'{}')}, b'{}', 'secret')
            with self.assertRaises(Unauthorized):
                verify(source, {}, b'{}', '')

    def test_slack_filters_bots_and_unknown_senders(self):
        payload = {'type':'event_callback','event_id':'E1','team_id':'T1','event':{'type':'message','channel':'C1','user':'U1','ts':'1','text':'work'}}
        e = normalize('slack', payload)
        config = {'responsibilities':[{'id':'s','enabled':True,'source':'slack','channels':['C1'],'senders':['U2']}]}
        self.assertIsNone(select_responsibility(config,e))
        config['responsibilities'][0]['senders']=['U1']
        self.assertIsNotNone(select_responsibility(config,e))
        payload['event']['bot_id']='B1'
        self.assertIsNone(normalize('slack',payload))

    def test_invalid_local_correlation(self):
        with self.assertRaises(ValueError):
            normalize('local', {'id':'x','title':'x','text':'x','correlation':{'bad':'data'}})


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.config = copy.deepcopy(CONFIG); self.config['engine']='demo'
        self.rt = Runtime(self.tmp.name, self.config)
        self.rt.start(); self.addCleanup(self.rt.close)

    def test_demo_event_approval_continuation(self):
        tid = self.rt.ingest('local', {'id':'demo','title':'Demo','text':'Test'})['task_id']
        a = wait_for(lambda:self.rt.status()['approvals'])[0]
        self.rt.store.decide(a['id'],a['scope_hash'],'approve','Write it')
        self.rt.wake.set()
        t = wait_for(lambda:next((t for t in self.rt.status()['tasks'] if t['state']=='ready_for_review'),None))
        self.assertEqual(t['id'],tid)
        self.assertEqual(t['result']['artifacts'][0]['path'],'demo-result.md')
        self.assertTrue((Path(self.tmp.name)/'workspaces'/tid/'demo-result.md').is_file())

    def test_second_runtime_cannot_claim_same_directory(self):
        other = Runtime(self.tmp.name,self.config)
        with self.assertRaises(RuntimeError):
            other.start()

    def test_paused_inbox_durable_without_runner(self):
        self.rt.store.pause(True)
        self.rt.ingest('local', {'id':'paused','title':'Paused','text':'Wait'})
        time.sleep(.1)
        self.assertEqual(self.rt.status()['tasks'][0]['state'],'queued')
        self.assertFalse(any(r['kind']=='run_started' for r in self.rt.status()['receipts']))


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.rt=Runtime(self.tmp.name,CONFIG); self.rt.store.pause(True)
        self.server=make_server(self.rt,0,{'slack':'test-secret'})
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.addCleanup(self.cleanup_server)
        self.base=f'http://127.0.0.1:{self.server.server_port}'
        self.owner=(Path(self.tmp.name)/'owner.key').read_text()
        self.ingress=(Path(self.tmp.name)/'ingress.key').read_text()

    def cleanup_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def request(self,path,token=None,body=None,headers=None):
        h={'Content-Type':'application/json'}
        if token: h['Authorization']='Bearer '+token
        h.update(headers or {})
        req=Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=h)
        try:
            with urlopen(req,timeout=2) as r: return r.status,json.load(r)
        except HTTPError as e:
            with e: return e.code,json.load(e)

    def test_ingress_cannot_read_or_approve(self):
        self.assertEqual(self.request('/api/status',self.ingress)[0],403)
        self.assertEqual(self.request('/api/decision',self.ingress,{})[0],403)
        self.assertEqual(self.request('/events/local',self.ingress,{'id':'1','title':'t','text':'hello'})[0],202)
        self.assertEqual(self.request('/api/status',self.owner)[1]['events'],1)

    def test_cross_origin_owner_request_rejected(self):
        self.assertEqual(self.request('/api/control',self.owner,{'action':'resume'},{'Origin':'https://evil.example'})[0],403)
        self.assertTrue(self.rt.store.paused())

    def test_artifact_requires_owner_and_blocks_escape(self):
        tid='a'*32
        work=self.rt.root/'workspaces'/tid
        work.mkdir(parents=True)
        (work/'result.md').write_text('Verified local result')
        (self.rt.root/'outside.txt').write_text('Private outside content')
        (work/'escape.txt').symlink_to(self.rt.root/'outside.txt')
        route='/api/artifact?task='+tid+'&path='
        self.assertEqual(self.request(route+'result.md',self.ingress)[0],403)
        self.assertEqual(self.request(route+'result.md',self.owner)[1]['text'],'Verified local result')
        for path in ('../../outside.txt','escape.txt','missing.txt'):
            self.assertEqual(self.request(route+path,self.owner)[0],400)

    def test_memory_and_feedback_require_owner(self):
        self.assertEqual(self.request('/api/memory',self.ingress,{'scope':'all','text':'bad'})[0],403)
        self.assertEqual(self.request('/api/feedback',self.ingress,{'id':'a'*32,'text':'bad'})[0],403)
        self.assertEqual(self.rt.store.memories(),[])

    def test_slack_signed_challenge(self):
        body={'type':'url_verification','challenge':'challenge'}; raw=json.dumps(body).encode(); ts=str(int(time.time()))
        headers={'X-Slack-Request-Timestamp':ts,'X-Slack-Signature':'v0='+signature('test-secret',b'v0:'+ts.encode()+b':'+raw)}
        self.assertEqual(self.request('/hooks/slack',body=body,headers=headers),(200,{'challenge':'challenge'}))
        self.assertEqual(self.request('/hooks/slack',body=body)[0],403)

    def test_unsigned_webhook_no_ingestion(self):
        self.assertEqual(self.request('/hooks/github',body={})[0],403)
        self.assertEqual(self.rt.status()['events'],0)


class AdapterTests(unittest.TestCase):
    def test_both_result_formats(self):
        result={'summary':'Done','status':'ready','question':'','evidence':['result.md']}
        claude=json.dumps({'structured_output':result,'total_cost_usd':.1})
        codex=json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(result)}})+'\n'+json.dumps({'type':'turn.completed','usage':{'output_tokens':10}})
        self.assertEqual(engines.parse_result('claude',claude)[0],result)
        self.assertEqual(engines.parse_result('codex',codex)[0],result)

    def test_model_failure_not_success(self):
        with self.assertRaises(RuntimeError):
            engines.parse_result('codex','{"type":"turn.failed","error":"failed"}')
        with self.assertRaises(ValueError):
            engines.parse_result('claude','{"structured_output":{"summary":"done"}}')


if __name__=='__main__':
    unittest.main()
