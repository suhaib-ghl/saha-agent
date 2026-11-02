import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from saha_agent import paths
from saha_agent.__main__ import configuration
from saha_agent.connections import Connections
from saha_agent.runtime import Runtime
from saha_agent.server import make_server

ROOT=Path(__file__).resolve().parents[1]


class PortabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.env={k:v for k,v in os.environ.items() if k not in {'SAHA_HOME','SAHA_CONFIG','SAHA_STATE'}}
        self.env['SAHA_HOME']=str(self.root/'profile')

    def cli(self,*args):
        return subprocess.run([sys.executable,'-m','saha_agent',*args],cwd=ROOT,env=self.env,
                              capture_output=True,text=True,check=True,timeout=15)

    def test_init_is_per_profile_and_never_overwrites_config(self):
        self.cli('init')
        config=self.root/'profile/config.json'
        c=configuration(config)
        self.assertEqual(c['engine'],'demo')
        self.assertFalse(any(v['enabled'] for v in c['integrations'].values()))
        c['engine']='claude';config.write_text(json.dumps(c))
        self.cli('init','--engine','codex')
        self.assertEqual(configuration(config)['engine'],'claude')
        self.env['SAHA_HOME']=str(self.root/'another-profile')
        self.cli('init','--engine','codex')
        self.assertEqual(configuration(self.root/'another-profile/config.json')['engine'],'codex')
        self.assertEqual(configuration(config)['engine'],'claude')

    def test_paths_and_mcp_share_profile_defaults(self):
        with patch.dict(os.environ,self.env,clear=True):
            self.assertEqual(paths.config(),self.root/'profile/config.json')
            self.assertEqual(paths.state(),self.root/'profile/state')
            with patch.dict(os.environ,{'SAHA_STATE':str(self.root/'explicit')}):
                self.assertEqual(paths.state(),self.root/'explicit')

    def test_service_files_use_current_profile_and_generic_identity(self):
        self.cli('init')
        self.cli('service-file','--format','launchd')
        f=self.root/'profile/state/io.saha.agent.plist'
        data=plistlib.loads(f.read_bytes())
        self.assertEqual(data['Label'],'io.saha.agent')
        self.assertIn(str(self.root/'profile/config.json'),data['ProgramArguments'])
        self.assertIn(str(self.root/'profile/state'),data['ProgramArguments'])
        self.cli('service-file','--format','systemd')
        text=(self.root/'profile/state/io.saha.agent.service').read_text()
        self.assertIn('ExecStart=',text)
        self.assertIn(str(self.root/'profile/config.json'),text)

    def test_packaged_and_repository_defaults_match(self):
        self.assertEqual(json.loads((ROOT/'config.example.json').read_text()),
                         json.loads((ROOT/'saha_agent/defaults/config.example.json').read_text()))

    def test_local_server_starts_without_dns(self):
        config=json.loads((ROOT/'config.example.json').read_text())
        runtime=Runtime(self.root/'state',config)
        with patch('socket.getfqdn',side_effect=AssertionError('DNS is unavailable')):
            server=make_server(runtime,0)
            self.assertEqual(server.server_name,'localhost')
            server.server_close()

    def test_enabled_integrations_require_explicit_identity_and_scope(self):
        c=json.loads((ROOT/'config.example.json').read_text())
        c['integrations']['github']['enabled']=True
        path=self.root/'config.json';path.write_text(json.dumps(c))
        with self.assertRaisesRegex(ValueError,'github'):configuration(path)
        c['integrations']['github']['login']='test-owner'
        c['integrations']['github']['pull_requests']=[{'repository':'unexpected/repo','number':42}]
        path.write_text(json.dumps(c))
        with self.assertRaisesRegex(ValueError,'allowlisted'):configuration(path)

    def test_clickup_does_not_scan_unconfigured_credentials(self):
        c=Connections(Mock(config={'integrations':{'clickup':{}}}))
        with patch('pathlib.Path.read_text') as read:
            with self.assertRaisesRegex(RuntimeError,'token_file'):c.clickup('/user')
            read.assert_not_called()

    def test_clickup_uses_explicit_private_token_file(self):
        token=self.root/'token';token.write_text('synthetic-unit-test-token')
        c=Connections(Mock(config={'integrations':{'clickup':{'token_file':str(token)}}}))
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=b'{"user":{"id":1}}'
        with patch('saha_agent.connections.urllib.request.urlopen',return_value=response) as fetch:
            self.assertEqual(c.clickup('/user')['user']['id'],1)
            self.assertEqual(fetch.call_args.args[0].get_header('Authorization'),'synthetic-unit-test-token')

    def test_export_excludes_private_records_and_local_config(self):
        from scripts.package import source_files
        files={p.relative_to(ROOT).as_posix() for p in source_files()}
        self.assertIn('pyproject.toml',files)
        self.assertIn('saha_agent/defaults/config.example.json',files)
        self.assertNotIn('config.local.json',files)
        self.assertFalse(any(p.startswith(('.runtime/','.private/','.git/')) for p in files))
        self.assertFalse(any(p.endswith(('.jpg','.sqlite3','.key')) for p in files))
