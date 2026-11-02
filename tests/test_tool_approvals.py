import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest

from unittest.mock import patch
from saha_agent.app_server import Client, supported_request, command
from saha_agent.store import Conflict, Store
from test_runtime import RULE, event, wait_for
import test_runtime as helpers


REQUEST = {'serverName': 'cua_repl', 'threadId': 't', 'turnId': 'turn', 'mode': 'form',
           'message': 'Allow Computer Use to use "Slack"?', 'requestedSchema': {'type': 'object', 'properties': {}},
           '_meta': {'connector_id': 'computer-use', 'codex_approval_kind': 'mcp_tool_call',
                     'tool_params': {'app': 'com.tinyspeck.slackmacgap'}}}


class LiveApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.ingest(event(), RULE, 'desktop')
        self.task = self.store.claim(60, 20)

    def request(self):
        ident = self.store.request_tool_approval(self.task, REQUEST, time.time()+30)
        a = self.store.snapshot()['tool_approvals'][0]
        self.assertEqual(a['id'], ident)
        self.assertNotIn('lease', a)
        return a

    def test_waiting_worker_receives_owner_answer_without_restart(self):
        result = []
        worker = threading.Thread(target=lambda: result.append(self.store.await_tool_approval(
            self.task, REQUEST, time.time()+3, lambda: False)))
        worker.start()
        a = wait_for(lambda: self.store.snapshot()['tool_approvals'])[0]
        self.store.tool_decision(a['id'], a['scope_hash'], 'accept')
        worker.join(4)
        self.assertEqual(result, ['accept'])
        task = self.store.snapshot()['tasks'][0]
        self.assertEqual((task['state'], task['attempts']), ('running', 1))
        with self.assertRaises(Conflict):
            self.store.tool_decision(a['id'], a['scope_hash'], 'accept')

    def test_expiry_wrong_scope_pause_and_cancellation_cannot_grant(self):
        a = self.request()
        with self.assertRaises(Conflict): self.store.tool_decision(a['id'], 'wrong', 'accept')
        self.store.pause(True)
        with self.assertRaises(Conflict): self.store.tool_decision(a['id'], a['scope_hash'], 'accept')
        self.store.pause(False)
        with self.store.transaction() as db:
            db.execute('UPDATE tool_approvals SET expires=0')
        with self.assertRaises(Conflict): self.store.tool_decision(a['id'], a['scope_hash'], 'accept')
        self.store.control_task(self.task['id'], 'cancel')
        self.assertEqual(self.store.snapshot()['tool_approvals'], [])
        with self.assertRaises(Conflict): self.store.tool_decision(a['id'], a['scope_hash'], 'accept')

    def test_feedback_and_restart_invalidate_requests(self):
        a = self.request()
        self.store.feedback(self.task['id'], 'Updated task scope')
        with self.assertRaises(Conflict): self.store.tool_decision(a['id'], a['scope_hash'], 'accept')
        self.assertEqual(self.store.snapshot()['tool_approvals'], [])
        self.store.recover()
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT decision FROM tool_approvals').fetchone()[0], 'cancel')

    def test_owner_continuation_resets_attempts_but_not_daily_budget(self):
        with self.store.transaction() as db:
            db.execute('UPDATE tasks SET attempts=3')
        self.store.finish(self.task, {'question': 'Continue local work?'}, 'waiting_approval')
        a = self.store.snapshot()['approvals'][0]
        self.store.decide(a['id'], a['scope_hash'], 'approve', 'Proceed')
        self.assertIsNone(self.store.claim(60, 1))
        self.assertEqual(self.store.claim(60, 20)['attempts'], 1)

    def test_unanswered_request_cancels_on_timeout(self):
        self.assertEqual(self.store.await_tool_approval(self.task, REQUEST, time.time()+.1, lambda: False), 'cancel')
        self.assertEqual(self.store.snapshot()['tool_approvals'], [])


class ProtocolTests(unittest.TestCase):
    def test_notifications_before_rpc_response_are_retained(self):
        with tempfile.TemporaryDirectory() as root:
            err = Path(root)/'stderr'; err.touch()
            process = SimpleNamespace(stdin=io.BytesIO())
            client = Client(process, io.StringIO(), err, time.time()+5, lambda: False, lambda p: 'decline')
            notice = {'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'text': 'done'}}}
            client.buffer = (json.dumps(notice)+'\n'+json.dumps({'id':1,'result':{'ok':True}})+'\n').encode()
            self.assertEqual(client.request('turn/start', {}), {'ok':True})
            self.assertEqual(client.receive(), notice)

    def test_adapter_disables_plugins_and_retains_sandbox(self):
        with tempfile.TemporaryDirectory() as root, patch('saha_agent.app_server.Path.home', return_value=Path(root)):
            cmd = command(['/codex','exec','-c','approval_policy="never"','-c','permissions.saha-local.network.enabled=false'])
        self.assertIn('features.plugins=false', cmd)
        self.assertIn('features.hooks=false', cmd)
        self.assertIn('permissions.saha-local.network.enabled=false', cmd)
        self.assertNotIn('approval_policy="never"', cmd)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', cmd)

    def test_only_supported_computer_approval_reaches_owner(self):
        self.assertTrue(supported_request(REQUEST))
        for change in ({'serverName': 'other'}, {'mode': 'url'}, {'_meta': {}},
                       {'requestedSchema': {'required': ['password']}}):
            self.assertFalse(supported_request({**REQUEST, **change}))

    def test_original_request_id_and_owner_decision_are_forwarded(self):
        client = Client.__new__(Client)
        sent = []; seen = []
        client.send = sent.append; client.check = lambda: None
        client.approve = lambda p: seen.append(p) or 'accept'
        client.server_request({'id': 'server-request-1', 'method': 'mcpServer/elicitation/request', 'params': REQUEST})
        self.assertEqual(seen, [REQUEST])
        self.assertEqual(sent, [{'id': 'server-request-1', 'result': {'action': 'accept', 'content': {}}}])
        client.server_request({'id': 2, 'method': 'mcpServer/elicitation/request', 'params': {**REQUEST, 'mode': 'url'}})
        self.assertEqual(len(seen), 1)
        self.assertEqual(sent[-1]['result']['action'], 'cancel')


class ToolHTTPTests(unittest.TestCase):
    setUp = helpers.HTTPTests.setUp
    cleanup_server = helpers.HTTPTests.cleanup_server
    request = helpers.HTTPTests.request

    def test_tool_decisions_require_owner_and_current_scope(self):
        self.rt.store.pause(False)
        self.rt.store.ingest(event(), RULE, 'desktop')
        task = self.rt.store.claim(60, 20)
        self.rt.store.request_tool_approval(task, REQUEST, time.time()+30)
        a = self.rt.store.snapshot()['tool_approvals'][0]
        body = {'id': a['id'], 'scope_hash': a['scope_hash'], 'decision': 'accept'}
        self.assertEqual(self.request('/api/tool-decision', self.ingress, body)[0], 403)
        self.assertEqual(self.request('/api/tool-decision', self.owner, body, {'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('/api/tool-decision', self.owner, body)[0], 200)
        self.assertEqual(self.request('/api/tool-decision', self.owner, body)[0], 409)
