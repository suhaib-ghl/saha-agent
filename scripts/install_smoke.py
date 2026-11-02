"""Exercise an installed wheel from outside the checkout, with synthetic state."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


def main():
    python=str(Path(sys.argv[1]).absolute())
    with tempfile.TemporaryDirectory(prefix='saha-install-') as temp:
        work=Path(temp)
        env={k:v for k,v in os.environ.items() if k not in {'SAHA_HOME','SAHA_CONFIG','SAHA_STATE','PYTHONPATH'}}
        env['SAHA_HOME']=str(work/'profile')
        def cli(*args):
            return subprocess.run([python,'-I','-m','saha_agent',*args],cwd=work,env=env,
                                  capture_output=True,text=True,check=True,timeout=15).stdout
        cli('init')
        config=work/'profile/config.json'
        data=json.loads(config.read_text())
        assert data['engine']=='demo'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));data['port']=sock.getsockname()[1]
        config.write_text(json.dumps(data))
        base='http://127.0.0.1:'+str(data['port'])
        state=work/'profile/state'
        def api(route,body=None):
            token=(state/'owner.key').read_text().strip()
            req=Request(base+route,data=json.dumps(body).encode() if body is not None else None,
                        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            with urlopen(req,timeout=2) as response:return json.load(response)
        def wait(fn):
            end=time.monotonic()+15
            last_error=None
            while time.monotonic()<end:
                if proc.poll() is not None:
                    raise AssertionError('Installed server exited: '+(work/'service.log').read_text())
                try:
                    result=fn()
                    if result:return result
                except (URLError,OSError) as exc:last_error=repr(exc)
                time.sleep(.05)
            raise AssertionError('Installed runtime did not reach expected state. Last request error: '+str(last_error)+'; server log: '+(work/'service.log').read_text())
        with (work/'service.log').open('w') as log:
            proc=subprocess.Popen([python,'-I','-m','saha_agent','serve'],cwd=work,env=env,stdout=log,stderr=log)
            try:
                wait(lambda:api('/api/status'))
                with urlopen(base,timeout=2) as response:assert b'Saha' in response.read()
                with urlopen(base+'/app.js',timeout=2) as response:assert b'tool-decision' in response.read()
                job=json.loads(cli('submit','Synthetic install smoke test','--engine','demo'))['task_id']
                approval=wait(lambda:api('/api/status')['approvals'])[0]
                cli('decide',approval['id'],'approve','--note','Write the synthetic local demo note')
                task=wait(lambda:next((t for t in api('/api/status')['tasks'] if t['id']==job and t['state']=='ready_for_review'),None))
                assert task['attempts']==1
                assert 'local continuation completed.' in (state/'workspaces'/job/'demo-result.md').read_text()
                api('/api/task-control',{'id':job,'action':'complete'})
                assert api('/api/status')['tasks'][0]['state']=='completed'
                packet={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'saha_status','arguments':{}}}
                mcp=subprocess.run([python,'-I','-m','saha_agent.mcp'],input=json.dumps(packet)+'\n',cwd=work,env=env,capture_output=True,text=True,check=True)
                content=json.loads(mcp.stdout)['result']['content'][0]['text']
                assert json.loads(content)['counts']['completed']==1
                entry=subprocess.check_output([python,'-I','-c','import pathlib,saha_agent; print(pathlib.Path(saha_agent.__file__).parent / "_entrypoints/context_server.py")'],cwd=work,text=True).strip()
                context=subprocess.run([python,entry,str(config)],input=json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/list'})+'\n',cwd=work,env=env,capture_output=True,text=True,check=True)
                assert len(json.loads(context.stdout)['result']['tools'])==2
                cli('service-file','--format','launchd')
                cli('service-file','--format','systemd')
                cli('pause');assert api('/api/status')['paused']
                cli('resume');assert not api('/api/status')['paused']
                cli('stop');proc.wait(timeout=15)
                assert proc.returncode==0
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait()
        print('PASS: installed wheel, packaged UI/defaults, isolated profile, demo decision/continuation, artifact, review, MCP/context entry points, service definitions, pause/resume/stop')


if __name__=='__main__':main()
