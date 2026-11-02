from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit, parse_qs

from .events import Unauthorized, verify
from .store import Conflict

WEB = Path(__file__).resolve().parent / 'web'
MAX_BODY = 256_000


def private_key(path):
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(secrets.token_urlsafe(32))
    except FileExistsError:
        pass
    Path(path).chmod(0o600)
    return Path(path).read_text().strip()


def make_server(runtime, port, source_secrets=None):
    owner = private_key(runtime.root / 'owner.key')
    ingress = private_key(runtime.root / 'ingress.key')
    sources = source_secrets or {s: os.environ.get(f'SAHA_{s.upper()}_SECRET', '') for s in ('slack', 'github', 'clickup')}

    class Handler(BaseHTTPRequestHandler):
        server_version = 'Saha/0.1'

        def log_message(self, fmt, *args):
            pass  # Do not log tokens, request bodies, or conversation content.

        def reply(self, code, value, content_type='application/json'):
            body = json.dumps(value).encode() if content_type == 'application/json' else value
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def host_ok(self):
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in allowed:
                raise Unauthorized('Invalid host')
            origin = self.headers.get('Origin')
            if origin and origin not in {'http://' + x for x in allowed}:
                raise Unauthorized('Cross-origin requests are not allowed')

        def auth(self, token):
            supplied = self.headers.get('Authorization', '')
            if not secrets.compare_digest(supplied, 'Bearer ' + token):
                raise Unauthorized('Authentication required')

        def body(self):
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > MAX_BODY:
                raise ValueError('Invalid request size')
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('Expected application/json')
            self.connection.settimeout(5)
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError('Incomplete request body')
            return body

        def do_GET(self):
            try:
                self.host_ok()
                path = urlsplit(self.path).path
                if path == '/health':
                    return self.reply(200, {'ok': True, 'service': 'saha-agent'})
                if path in {'/', '/app.js', '/style.css'}:
                    file = WEB / {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[path]
                    mime = {'/': 'text/html; charset=utf-8', '/app.js': 'text/javascript; charset=utf-8', '/style.css': 'text/css; charset=utf-8'}[path]
                    return self.reply(200, file.read_bytes(), mime)
                self.auth(owner)
                if path == '/api/status':
                    data = runtime.status()
                    data['connections'] = {s: bool(v) for s, v in sources.items()}
                    data['integrations'] = runtime.connections.snapshot()
                    data['memories'] = runtime.store.memories()
                    return self.reply(200, data)
                if path == '/api/artifact':
                    query=parse_qs(urlsplit(self.path).query)
                    tid=query.get('task',[''])[0];relative=query.get('path',[''])[0]
                    if len(tid)!=32 or any(c not in '0123456789abcdef' for c in tid):
                        return self.reply(400,{'error':'Invalid task'})
                    root=(runtime.root/'workspaces'/tid).resolve()
                    target=(root/relative).resolve()
                    if not target.is_relative_to(root) or not target.is_file() or target.stat().st_size>200000:
                        return self.reply(400,{'error':'Artifact unavailable or too large'})
                    return self.reply(200,{'path':relative,'text':target.read_text(errors='replace')})
                self.reply(404, {'error': 'Not found'})
            except Unauthorized as e:
                self.reply(403, {'error': str(e)})

            except (OSError, ValueError):
                self.reply(400, {'error': 'Requested resource is unavailable'})

        def do_POST(self):
            try:
                self.host_ok()
                path = urlsplit(self.path).path
                if path.startswith('/hooks/'):
                    source = path.removeprefix('/hooks/')
                    raw = self.body()
                    verify(source, self.headers, raw, sources.get(source, ''))
                    data = json.loads(raw)
                    if source == 'slack' and data.get('type') == 'url_verification':
                        return self.reply(200, {'challenge': data['challenge']})
                    return self.reply(200, runtime.ingest(source, data, self.headers))
                if path == '/events/local':
                    self.auth(ingress)
                    return self.reply(202, runtime.ingest('local', json.loads(self.body())))
                self.auth(owner)
                data = json.loads(self.body())
                if not isinstance(data, dict):
                    raise ValueError('Expected JSON object')
                if path == '/api/memory':
                    if data.get('action')=='retire':
                        runtime.store.forget(data['id'])
                        return self.reply(200,{'ok':True})
                    return self.reply(200,runtime.store.remember(data['scope'],data['text']))
                if path == '/api/feedback':
                    runtime.store.feedback(data['id'],data['text'])
                    runtime.wake.set()
                    return self.reply(200,{'ok':True})
                if path == '/api/tasks':
                    return self.reply(202, runtime.ingest('local', data, engine=data.get('engine')))
                if path == '/api/decision':
                    tid = runtime.store.decide(data['id'], data['scope_hash'], data['decision'], data.get('note', ''))
                    runtime.wake.set()
                    return self.reply(200, {'task_id': tid})
                if path == '/api/tool-decision':
                    runtime.store.tool_decision(data['id'], data['scope_hash'], data['decision'])
                    return self.reply(200, {'ok': True})
                if path == '/api/control':
                    if data.get('action') == 'stop':
                        runtime.stop.set()
                        runtime.wake.set()
                        self.reply(200, {'stopping': True})
                        threading.Thread(target=self.server.shutdown, daemon=True).start()
                        return
                    if data.get('action') not in {'pause', 'resume'}:
                        raise ValueError('Expected pause or resume')
                    runtime.store.pause(data['action'] == 'pause')
                    runtime.wake.set()
                    return self.reply(200, {'paused': runtime.store.paused()})
                if path == '/api/task-control':
                    runtime.store.control_task(data['id'], data['action'], data.get('engine'))
                    runtime.wake.set()
                    return self.reply(200, {'ok': True})
                self.reply(404, {'error': 'Not found'})
            except Unauthorized as e:
                self.reply(403, {'error': str(e)})
            except Conflict as e:
                self.reply(409, {'error': str(e)})
            except (ValueError, KeyError, TypeError) as e:
                self.reply(400, {'error': str(e)})
            except Exception:
                self.reply(500, {'error': 'Request failed; no acknowledgement of durable ingestion'})

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)
