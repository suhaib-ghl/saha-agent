"""Use the installed official CUA MCP server without copying turn credentials.

Native and browser capabilities are tested independently. An unavailable browser
surface is not bypassed through a different automation interface.
"""
import json
import os
from pathlib import Path
import select
import subprocess
import time


def configuration(manifest):
    c=json.loads(Path(manifest).read_text())['mcpServers']['cua_repl']
    return {k:c[k] for k in ('command','args','env') if k in c}


def probe(manifest):
    c=configuration(manifest)
    env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','USER','TMPDIR','LANG'}}
    env.update(c.get('env',{}))
    p=subprocess.Popen([c['command'],*c.get('args',[])],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,env=env)
    def request(ident,method,params):
        p.stdin.write(json.dumps({'jsonrpc':'2.0','id':ident,'method':method,'params':params})+'\n');p.stdin.flush()
        end=time.monotonic()+15
        while time.monotonic()<end:
            if not select.select([p.stdout],[],[],.5)[0]:continue
            line=p.stdout.readline()
            if not line:raise RuntimeError('Computer-use service exited')
            try:data=json.loads(line)
            except ValueError:continue
            if data.get('id')==ident:
                if data.get('error'):raise RuntimeError('Computer-use MCP rejected request')
                return data['result']
        raise RuntimeError('Computer-use service timed out')
    try:
        request(1,'initialize',{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'saha','version':'0.2'}})
        p.stdin.write(json.dumps({'jsonrpc':'2.0','method':'notifications/initialized'})+'\n');p.stdin.flush()
        result=request(2,'tools/call',{'name':'js','arguments':{'code':'await cua.getState();','title':'Check Saha native app availability'}})
        if result.get('isError'):raise RuntimeError('Computer-use access unavailable')
        text='\n'.join(x.get('text','') for x in result.get('content',[]) if x.get('type')=='text')
        state=None
        for line in text.splitlines():
            if line.startswith('{"apps":'):
                state=json.loads(line)
        if state is None:raise RuntimeError('No computer state returned')
        native=bool(state.get('apps'))
        browser=bool(state.get('browsers'))
        return {'status':'partial' if native or browser else 'unavailable',
                'native_apps':native,'browser':browser,
                'detail':'App discovery responds; access to individual apps is not verified. Browser requires a current authorized Codex turn.' if native and not browser else 'Surface discovery responds; actual app access requires a successful task.' if native or browser else 'Native apps unavailable; keep the Mac awake and check app permissions'}
    finally:
        p.terminate()
        try:p.wait(timeout=3)
        except subprocess.TimeoutExpired:p.kill();p.wait()


def toml(value):
    if isinstance(value,dict):return '{'+','.join(json.dumps(k)+'='+toml(v) for k,v in value.items())+'}'
    return json.dumps(value)


def codex_options(manifest):
    cfg=configuration(manifest)
    return ['-c','mcp_servers.cua_repl='+toml(cfg)]
