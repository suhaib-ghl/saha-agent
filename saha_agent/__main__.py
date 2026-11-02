import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import threading
import urllib.request
import uuid
import webbrowser

from .runtime import Runtime
from .server import make_server
from . import __version__, paths

PACKAGE = Path(__file__).resolve().parents[1]


def configuration(path):
    config = json.loads(Path(path).read_text())
    if config.get('engine') not in {'codex', 'claude', 'demo', 'team', 'desktop'}:
        raise ValueError('Unsupported engine')
    for key, low, high in [('port', 1024, 65535), ('timeout_seconds', 5, 3600), ('max_attempts', 1, 10), ('max_runs_per_day', 1, 1000)]:
        if type(config.get(key)) is not int or not low <= config[key] <= high:
            raise ValueError(f'Invalid {key}')
    if not 0 < config.get('claude_budget_usd', 0) <= 50:
        raise ValueError('Invalid Claude budget')
    if not isinstance(config.get('responsibilities'), list):
        raise ValueError('Responsibilities must be an array')
    seen = set()
    for r in config['responsibilities']:
        if not all(isinstance(r.get(k), str) and r[k] for k in ('id', 'source', 'instructions')) or type(r.get('enabled')) is not bool:
            raise ValueError('Invalid responsibility')
        if r['id'] in seen or r['source'] not in {'local', 'slack', 'github', 'clickup'}:
            raise ValueError('Duplicate or unsupported responsibility')
        seen.add(r['id'])
        if r['enabled'] and r['source']=='slack' and not all(isinstance(r.get(k),list) and r[k] and all(isinstance(v,str) and v for v in r[k]) for k in ('channels','senders')):
            raise ValueError('Enabled Slack responsibilities require channel and sender allowlists')
        if r['enabled'] and r['source']=='clickup' and not str(r.get('assignee_id','')).strip():
            raise ValueError('Enabled ClickUp responsibilities require your assignee_id')
        if r['enabled'] and r['source']=='github' and not r.get('repositories'):
            raise ValueError('Enabled GitHub responsibilities require repository allowlists')
    for name,cfg in config.get('integrations',{}).items():
        if not isinstance(cfg,dict) or type(cfg.get('enabled')) is not bool:
            raise ValueError('Integration entries need a boolean enabled field')
        if not cfg['enabled']:continue
        required={'clickup':('workspace_id','user_id'), 'github':('login',),
                  'slack':('team_id','credentials_file'), 'computer':('manifest',)}
        if name not in required or any(not str(cfg.get(k,'')).strip() for k in required[name]):
            raise ValueError(f'Complete the {name} integration settings before enabling it')
        if name=='clickup' and not (cfg.get('token_file') or (cfg.get('credential_config') and cfg.get('credential_server'))):
            raise ValueError('ClickUp needs a private token_file')
        if name=='github':
            allowed={repo for rule in config['responsibilities'] if rule['source']=='github' for repo in rule.get('repositories',[])}
            for pr in cfg.get('pull_requests',[]):
                if pr.get('repository') not in allowed or type(pr.get('number')) is not int or pr['number']<1:
                    raise ValueError('Each watched PR needs a positive number and an allowlisted repository')
    return config


def request(root, config, route, body=None):
    token = (root / 'owner.key').read_text().strip()
    req = urllib.request.Request(f"http://127.0.0.1:{config['port']}{route}",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description='Saha event-driven local work agent')
    parser.add_argument('--version', action='version', version='Saha '+__version__)
    parser.add_argument('--state', type=Path, default=paths.state())
    parser.add_argument('--config', type=Path, default=paths.config())
    sub = parser.add_subparsers(dest='cmd', required=True)
    init = sub.add_parser('init')
    init.add_argument('--engine',choices=['demo','codex','claude','team'],default='demo')
    sub.add_parser('serve')
    sub.add_parser('status')
    sub.add_parser('open')
    sub.add_parser('doctor')
    sub.add_parser('pause')
    sub.add_parser('resume')
    sub.add_parser('stop')
    service = sub.add_parser('service-file', help='Generate a per-user service definition; does not install it')
    service.add_argument('--format',choices=['launchd','systemd'],default='launchd' if sys.platform=='darwin' else 'systemd')
    service.add_argument('--label',default='io.saha.agent')
    task = sub.add_parser('submit')
    task.add_argument('text')
    task.add_argument('--title', default='Local work request')
    task.add_argument('--engine', choices=['codex', 'claude', 'demo', 'team', 'desktop'])
    decision = sub.add_parser('decide')
    decision.add_argument('id')
    decision.add_argument('decision', choices=['approve', 'deny'])
    decision.add_argument('--note', default='')
    args = parser.parse_args()
    os.umask(0o077)
    root = args.state.expanduser().resolve()
    args.config = args.config.expanduser().resolve()
    if args.cmd == 'init':
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not args.config.exists():
            args.config.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            template=json.loads((Path(__file__).parent/'defaults/config.example.json').read_text())
            template['engine']=args.engine
            with args.config.open('x') as f: json.dump(template,f,indent=2);f.write('\n')
        print(f'Configuration: {args.config}\nState: {root}\nExisting configuration is preserved. Run saha doctor, then saha serve. In another terminal, run saha open.')
        return
    config = configuration(args.config)
    config['_config_path'] = str(args.config.resolve())
    if args.cmd == 'doctor':
        report = {'version':__version__, 'python': sys.version.split()[0], 'platform':sys.platform,
                  'configuration': str(args.config), 'state': str(root), 'engines': {},
                  'credentials':'Engine installation checks do not verify login or live source access.'}
        for engine in ('codex', 'claude'):
            executable = shutil.which(engine)
            report['engines'][engine] = {'installed': bool(executable), 'path': executable}
            if executable:
                p = subprocess.run([executable, '--version'], capture_output=True, text=True, timeout=10)
                report['engines'][engine]['version'] = p.stdout.strip()
        report['webhooks'] = {s: {'secret_configured': bool(os.environ.get(f'SAHA_{s.upper()}_SECRET')), 'responsibility_enabled': any(r['source'] == s and r['enabled'] for r in config['responsibilities'])} for s in ('slack', 'github', 'clickup')}
        computer=config.get('integrations',{}).get('computer',{})
        report['computer_use']={'configured':bool(computer.get('enabled')),'verified':False,
            'detail':'Optional official CUA host required; use a read-only desktop job to verify actual app access.'}
        if computer.get('enabled'):
            from .desktop import configuration as desktop_configuration
            try:
                desktop_configuration(Path(computer['manifest']).expanduser())
                report['computer_use']['manifest']='readable'
            except (OSError,ValueError,KeyError):report['computer_use']['manifest']='unavailable or invalid'
        print(json.dumps(report, indent=2))
        return
    if args.cmd == 'serve':
        runtime = Runtime(root, config)
        runtime.start()
        server = None
        try:
            server = make_server(runtime, config['port'])
            def shutdown(*_):
                runtime.stop.set()
                runtime.wake.set()
                threading.Thread(target=server.shutdown, daemon=True).start()
            signal.signal(signal.SIGTERM, shutdown)
            signal.signal(signal.SIGINT, shutdown)
            print(f"Saha listening on http://127.0.0.1:{server.server_port}; idle model calls: zero", flush=True)
            server.serve_forever(poll_interval=0.3)
        finally:
            if server:
                server.server_close()
            runtime.close()
        return
    if args.cmd == 'service-file':
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}',args.label):
            raise ValueError('Invalid service label')
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        argv=[sys.executable, '-m', 'saha_agent', '--state', str(root), '--config', str(args.config), 'serve']
        if args.format=='launchd':
            target=root/(args.label+'.plist')
            data = {'Label': args.label, 'ProgramArguments': argv, 'WorkingDirectory': str(PACKAGE), 'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 30, 'EnvironmentVariables': {'PATH': os.environ.get('PATH', '/usr/bin:/bin')}, 'StandardOutPath': str(root / 'service.log'), 'StandardErrorPath': str(root / 'service.stderr')}
            target.write_bytes(plistlib.dumps(data))
        else:
            target=root/(args.label+'.service')
            quote=lambda s:json.dumps(str(s).replace('%','%%').replace('$','$$'))
            target.write_text('[Unit]\nDescription=Saha local work agent\nAfter=network.target\n\n[Service]\nType=simple\nWorkingDirectory='+quote(PACKAGE)+'\nExecStart='+' '.join(quote(v) for v in argv)+'\nEnvironment='+quote('PATH='+os.environ.get('PATH','/usr/bin:/bin'))+'\nRestart=on-failure\nRestartSec=30\nUMask=0077\n\n[Install]\nWantedBy=default.target\n')
        print(target)
        return
    if args.cmd == 'open':
        key = (root / 'owner.key').read_text().strip()
        webbrowser.open(f"http://127.0.0.1:{config['port']}/#token={key}")
        print('Opened Saha dashboard. The browser removes the token from the address bar.')
        return
    if args.cmd == 'status':
        result = request(root, config, '/api/status')
    elif args.cmd in ('pause', 'resume', 'stop'):
        result = request(root, config, '/api/control', {'action': args.cmd})
    elif args.cmd == 'submit':
        result = request(root, config, '/api/tasks', {'id': uuid.uuid4().hex, 'title': args.title, 'text': args.text, 'engine': args.engine or config['engine']})
    else:
        snapshot = request(root, config, '/api/status')
        approval = next((a for a in snapshot['approvals'] if a['id'] == args.id), None)
        if not approval:
            raise ValueError('Pending approval not found')
        result = request(root, config, '/api/decision', {'id': args.id, 'scope_hash': approval['scope_hash'], 'decision': args.decision, 'note': args.note})
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
