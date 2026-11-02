import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from saha_agent import engines, mcp
from saha_agent.store import Store, Conflict
from test_runtime import CONFIG, RULE, event


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=Store(self.root)

    def test_mcp_stdio_discovery_and_call(self):
        messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}},
                  {'jsonrpc':'2.0','method':'notifications/initialized'},
                  {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                  {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'saha_status','arguments':{}}}]
        p=subprocess.run([sys.executable,str(Path(__file__).parents[1]/'scripts/mcp_server.py')],
                         input='\n'.join(map(json.dumps,messages))+'\n',capture_output=True,text=True,
                         env={**os.environ,'SAHA_STATE':str(self.root)},check=True,timeout=5)
        results=[json.loads(line) for line in p.stdout.splitlines()]
        self.assertEqual(len(results),3)
        self.assertEqual(results[0]['result']['protocolVersion'],'2025-06-18')
        self.assertEqual({t['name'] for t in results[1]['result']['tools']},{'saha_status','saha_tasks','saha_receipts'})
        self.assertFalse(results[2]['result']['isError'])

    def test_mcp_has_no_mutation_and_does_not_create_state(self):
        before=set(self.root.iterdir())
        r=mcp.handle({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'approve','arguments':{}}},self.root)
        self.assertTrue(r['result']['isError'])
        missing=self.root/'missing'
        self.assertFalse(mcp.read_tool('saha_status',{},missing)['initialized'])
        self.assertFalse(missing.exists())
        self.assertEqual(set(self.root.iterdir()),before)

    def test_completion_requires_ready_result(self):
        tid=self.store.ingest(event(),RULE,'demo')['task_id']
        with self.assertRaises(Conflict):self.store.control_task(tid,'complete')
        t=self.store.claim(60,20);self.store.finish(t,{'summary':'Delivered'},'ready_for_review')
        self.store.control_task(tid,'complete')
        self.assertEqual(self.store.snapshot()['tasks'][0]['state'],'completed')

    def task(self):
        self.store.ingest(event(),RULE,'codex');return self.store.claim(60,20)

    def test_timeout_terminates_child_and_preserves_log(self):
        task=self.task(); config={**CONFIG,'timeout_seconds':.3}
        code='import sys,time,os;sys.stdin.read();print(os.getpid(),flush=True);time.sleep(30)'
        with patch('saha_agent.engines.command',return_value=[sys.executable,'-c',code]):
            with self.assertRaisesRegex(RuntimeError,'time budget'): engines.run(task,self.root,config)
        log=next((self.root/'runs'/task['id']).glob('*.jsonl'))
        pid=int(log.read_text().strip())
        with self.assertRaises(ProcessLookupError):os.kill(pid,0)

    def test_cancel_terminates_child(self):
        task=self.task()
        with patch('saha_agent.engines.command',return_value=[sys.executable,'-c','import sys,time;sys.stdin.read();time.sleep(30)']):
            with self.assertRaisesRegex(RuntimeError,'cancelled'):engines.run(task,self.root,CONFIG,cancelled=lambda:True)

    def test_prompt_caps_event_context_and_marks_it_untrusted(self):
        task=self.task();task['events']=[{**event(str(n)), 'text':'x'*7000} for n in range(100)]
        text=engines.prompt(task)
        self.assertLess(len(text),130000)
        self.assertIn('UNTRUSTED EVENT DATA',text)
        self.assertEqual(text.count('[Truncated:'),20)

    def test_current_config_and_manifests_parse(self):
        root=Path(__file__).parents[1]
        for name in ['plugin.json','mcp.json','.claude-plugin/plugin.json','.mcp.json','hooks/hooks.json','config.example.json']:
            self.assertIsInstance(json.loads((root/name).read_text()),dict)
        for path in (root/'skills').glob('*/SKILL.md'):
            text=path.read_text();self.assertTrue(text.startswith('---\nname: '));self.assertIn('\ndescription: ',text)


if __name__=='__main__':unittest.main()
