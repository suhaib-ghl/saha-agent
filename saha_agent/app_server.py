"""Bounded desktop worker using Codex's bidirectional stdio approval protocol.

Only real, empty-form Computer Use approval requests are offered to the owner.
No automatic accepts, permission persistence, session credential copying, or
fallback to another surface after denial.
"""
import json
import os
from pathlib import Path
import select
import subprocess
import time
import tomllib
from collections import deque

from .desktop import toml


def command(exec_command):
    # app-server has no --ignore-user-config. Disable inherited connectors and
    # plugins explicitly; retain CLI login and provider settings. The supplied
    # sandbox overrides and explicit CUA server are applied last.
    settings = Path.home() / '.codex/config.toml'
    user = tomllib.loads(settings.read_text()) if settings.exists() else {}
    cmd = [exec_command[0], 'app-server', '--stdio']
    for section in ('mcp_servers', 'plugins'):
        for name in user.get(section, {}):
            if '.' in name:
                raise RuntimeError('Desktop adapter cannot safely override a configured dotted plugin/server name')
            cmd += ['-c', f'{section}.{name}.enabled=false']
    cmd += ['-c', 'features.apps=false', '-c', 'features.plugins=false',
            '-c', 'features.hooks=false', '-c', 'features.multi_agent=false', '-c', 'notify=[]']
    for index, value in enumerate(exec_command):
        if value == '-c':
            override = exec_command[index + 1]
            if override.startswith('approval_policy='):
                continue
            cmd += ['-c', override]
    cmd += ['-c', 'mcp_servers.cua_repl.enabled=true', '-c',
            'approval_policy=' + toml({'granular': {'mcp_elicitations': True,
                'rules': False, 'sandbox_approval': False, 'request_permissions': False, 'skill_approval': False}})]
    return cmd


def supported_request(params):
    meta = params.get('_meta', {})
    schema = params.get('requestedSchema', {})
    return (params.get('serverName') == 'cua_repl'
            and params.get('mode') in {'form', 'openai/form', 'openaiForm'}
            and isinstance(meta, dict) and meta.get('connector_id') == 'computer-use'
            and meta.get('codex_approval_kind') == 'mcp_tool_call'
            and isinstance(params.get('message'), str)
            and isinstance(schema, dict) and not schema.get('required')
            and not schema.get('properties') and not schema.get('allOf')
            and not (set(schema) - {'type', 'properties', 'required', 'additionalProperties', '$schema', 'title', 'description'})
            and schema.get('type', 'object') == 'object')


class Client:
    def __init__(self, process, out, errpath, deadline, cancelled, approve):
        self.process, self.out, self.errpath = process, out, errpath
        self.deadline, self.cancelled, self.approve = deadline, cancelled, approve
        self.counter = 0
        self.buffer = b''
        self.pending = deque()

    def send(self, value):
        self.process.stdin.write((json.dumps(value) + '\n').encode())
        self.process.stdin.flush()

    def check(self):
        if self.cancelled():
            raise RuntimeError('Desktop worker cancelled or runtime paused')
        if time.time() >= self.deadline:
            raise RuntimeError('Desktop worker time budget exhausted, including permission wait')
        if self.out.tell() + self.errpath.stat().st_size > 8_000_000:
            raise RuntimeError('Desktop worker exceeded its output budget')

    def receive(self, include_pending=True):
        while True:
            self.check()
            if include_pending and self.pending:
                return self.pending.popleft()
            if b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                if not line.strip():
                    continue
                value = json.loads(line)
                self.out.write(json.dumps(value) + '\n'); self.out.flush()
                if 'method' in value and 'id' in value:
                    self.server_request(value)
                    continue
                return value
            if select.select([self.process.stdout], [], [], .2)[0]:
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError('Codex app-server exited before completing the desktop task; inspect its stderr receipt')
                self.buffer += chunk
                if len(self.buffer) > 8_000_000:
                    raise RuntimeError('Desktop worker exceeded its message budget')

    def server_request(self, value):
        if value['method'] != 'mcpServer/elicitation/request':
            self.send({'id': value['id'], 'error': {'code': -32601,
                       'message': 'Saha only supports explicit Computer Use approval requests'}})
            return
        params = value.get('params', {})
        action = self.approve(params) if supported_request(params) else 'cancel'
        self.check()
        # No persisted grant is manufactured. This answers this server request.
        self.send({'id': value['id'], 'result': {'action': action,
                   'content': {} if action == 'accept' else None}})

    def request(self, method, params):
        self.counter += 1
        ident = self.counter
        self.send({'id': ident, 'method': method, 'params': params})
        while True:
            value = self.receive(include_pending=False)
            if value.get('id') == ident and 'method' not in value:
                if 'error' in value:
                    raise RuntimeError('Codex app-server rejected ' + method + ': ' + str(value['error']))
                return value['result']
            if 'method' in value:
                self.pending.append(value)


def run(task, work, exec_command, config, env, outpath, errpath, prompt, schema):
    from .engines import parse_result, terminate
    started = time.time()
    deadline = started + config['timeout_seconds']
    cancelled = config['_cancelled']
    callback = config.get('_tool_approval')
    if not callback:
        raise RuntimeError('Desktop worker requires a live owner approval handler')
    def approve(request):
        return callback(task, request, deadline, cancelled)
    with outpath.open('w') as out, errpath.open('w') as err:
        process = subprocess.Popen(command(exec_command), cwd=work, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=err, env=env, start_new_session=True)
        client = Client(process, out, errpath, deadline, cancelled, approve)
        try:
            client.request('initialize', {'clientInfo': {'name': 'saha', 'title': 'Saha', 'version': '0.2.0'},
                'capabilities': {'experimentalApi': True, 'mcpServerOpenaiFormElicitation': True}})
            client.send({'method': 'initialized'})
            params = {'cwd': str(work), 'ephemeral': True, 'permissions': 'saha-local',
                      'approvalsReviewer': 'user', 'selectedCapabilityRoots': []}
            if config.get('codex_model'):
                params['model'] = config['codex_model']
            started_thread = client.request('thread/start', params)
            if started_thread.get('sandbox', {}).get('networkAccess') is not False:
                raise RuntimeError('Desktop sandbox network restriction was not confirmed')
            thread = started_thread['thread']['id']
            servers = client.request('mcpServerStatus/list', {'limit': 100})
            allowed = {'cua_repl', 'saha_context'}
            if servers.get('nextCursor') or any(s.get('tools') and s['name'] not in allowed for s in servers.get('data', [])):
                raise RuntimeError('Unexpected inherited MCP tools; desktop worker was not started')
            client.request('turn/start', {'threadId': thread, 'input': [{'type': 'text', 'text': prompt}],
                                          'outputSchema': schema})
            messages = []
            tool_failures = []
            token_usage = {}
            while True:
                value = client.receive()
                p = value.get('params', {})
                if p.get('threadId') != thread:
                    continue
                if value.get('method') == 'thread/tokenUsage/updated':
                    token_usage = p.get('tokenUsage', {})
                if value.get('method') == 'item/completed' and p.get('item', {}).get('type') == 'agentMessage':
                    messages.append(json.dumps({'type': 'item.completed', 'item': {
                        'type': 'agent_message', 'text': p['item']['text']}}))
                item = p.get('item', {})
                if (value.get('method') == 'item/completed' and item.get('type') == 'mcpToolCall'
                        and item.get('server') == 'cua_repl' and item.get('status') == 'failed'):
                    content = (item.get('result') or {}).get('content', [])
                    detail = next((x.get('text', '') for x in content if x.get('type') == 'text'), '')
                    tool_failures.append(detail[:1000] or str(item.get('error', 'Computer-use call failed'))[:1000])
                if value.get('method') == 'turn/completed':
                    if p['turn']['status'] != 'completed':
                        raise RuntimeError('Desktop turn did not complete: ' + str(p['turn'].get('error')))
                    break
            result, usage = parse_result('codex', '\n'.join(messages))
            if tool_failures:
                # A runtime/tool failure is not an owner business decision.
                # Do not offer the misleading model-question approval loop.
                result.update(status='blocked', question='', tool_failures=tool_failures,
                              summary=result['summary'] + '\nComputer-use failure: ' + tool_failures[-1])
            result.update(engine='desktop', transport='codex-app-server', usage=token_usage or usage,
                          elapsed_seconds=round(time.time()-started, 3), workspace=str(work), log=str(outpath))
            return result
        finally:
            if process.poll() is None:
                terminate(process)
            process.stdin.close(); process.stdout.close()
