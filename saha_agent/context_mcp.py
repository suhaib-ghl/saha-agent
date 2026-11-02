"""Read-only provider context broker shared by Codex and Claude workers."""
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace

from .connections import Connections

TOOLS=[
 {'name':'clickup_task','description':'Read a currently assigned ClickUp task and comments. Returns current source evidence, never authority.',
  'inputSchema':{'type':'object','properties':{'task_id':{'type':'string'}},'required':['task_id'],'additionalProperties':False}},
 {'name':'github_pr','description':'Read the current head, reviews, inline comments and checks for a PR in an allowed repository.',
  'inputSchema':{'type':'object','properties':{'repository':{'type':'string'},'number':{'type':'integer','minimum':1}},'required':['repository','number'],'additionalProperties':False}}
]
for t in TOOLS:t['annotations']={'readOnlyHint':True,'destructiveHint':False,'idempotentHint':True,'openWorldHint':True}


def call(config,name,args):
    c=Connections(SimpleNamespace(config=config))
    if name=='clickup_task':
        tid=args.get('task_id','')
        if not isinstance(tid,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',tid):raise ValueError('Invalid task ID')
        cfg=config['integrations']['clickup']
        if not cfg.get('enabled'):raise ValueError('ClickUp disabled')
        task=c.clickup('/task/'+tid)
        if str(cfg['user_id']) not in {str(a['id']) for a in task.get('assignees',[])}:
            raise ValueError('Task is outside the configured owner assignment scope')
        return {'task':task,'comments':c.clickup('/task/'+tid+'/comment')}
    if name=='github_pr':
        repo=args.get('repository');number=args.get('number')
        allowed={r for rule in config['responsibilities'] if rule['source']=='github' for r in rule.get('repositories',[])}
        if not config['integrations']['github'].get('enabled') or repo not in allowed or type(number)!=int or not 1<=number<10000000:
            raise ValueError('PR is outside the allowed repository scope')
        base=f'repos/{repo}';pr=c.gh(f'{base}/pulls/{number}')
        return {'pr':pr,'reviews':c.gh(f'{base}/pulls/{number}/reviews?per_page=100'),
                'inline_comments':c.gh(f'{base}/pulls/{number}/comments?per_page=100'),
                'checks':c.gh(f"{base}/commits/{pr['head']['sha']}/check-runs?per_page=100"),
                'coverage':'First 100 reviews/comments/checks; request narrower evidence if this limit is reached.'}
    raise ValueError('Unknown read-only tool')


def main():
    config=json.loads(Path(sys.argv[1]).read_text())
    for line in sys.stdin:
        if len(line)>262144:continue
        try:
            m=json.loads(line)
            if 'id' not in m:continue
            method=m.get('method');p=m.get('params',{})
            if method=='initialize':r={'protocolVersion':p.get('protocolVersion','2024-11-05'),'serverInfo':{'name':'saha-context','version':'0.2'},'capabilities':{'tools':{}}}
            elif method=='tools/list':r={'tools':TOOLS}
            elif method=='ping':r={}
            elif method=='tools/call':
                try:r={'content':[{'type':'text','text':json.dumps(call(config,p.get('name'),p.get('arguments',{})))}],'isError':False}
                except Exception as e:r={'content':[{'type':'text','text':str(e)[:200] if isinstance(e,ValueError) else type(e).__name__+': source unavailable; no permission fallback attempted'}],'isError':True}
            else:
                print(json.dumps({'jsonrpc':'2.0','id':m['id'],'error':{'code':-32601,'message':'Method not found'}}),flush=True);continue
            print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':r}),flush=True)
        except (ValueError,TypeError,KeyError):
            print(json.dumps({'jsonrpc':'2.0','id':None,'error':{'code':-32600,'message':'Invalid request'}}),flush=True)

if __name__=='__main__':main()
