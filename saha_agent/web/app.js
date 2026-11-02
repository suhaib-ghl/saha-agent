const $ = id => document.getElementById(id);
const fragment = new URLSearchParams(location.hash.slice(1));
if (fragment.has('token')) { sessionStorage.setItem('saha-token', fragment.get('token')); history.replaceState(null, '', '/'); }
const token = sessionStorage.getItem('saha-token');
let paused = false, busy = false, engineInitialized = false;
function el(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }
function fail(e) { $('error').hidden = false; $('error').textContent = e.message; }
async function api(path, body) {
  const r = await fetch(path, {method: body === undefined ? 'GET' : 'POST', headers: {'Authorization': 'Bearer ' + token, 'Content-Type':'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  const value = await r.json(); if (!r.ok) throw new Error(value.error || 'Request failed'); return value;
}
function button(text, action) { const b = el('button', text); b.onclick = async () => { b.disabled = true; try { await action(); await refresh(); } catch(e) { fail(e); } finally { b.disabled = false; } }; return b; }
async function refresh() {
  if (!token || busy) return; busy = true;
  try {
    const opened = new Set([...document.querySelectorAll('#tasks details[open]')].map(d=>d.dataset.key));
    const drafts = new Map([...document.querySelectorAll('#tasks textarea')].map(t=>[t.dataset.task,t.value]));
    const s = await api('/api/status'); $('dashboard').hidden = false; $('login').hidden = true;
    if(!engineInitialized){$('engine').value=s.engine;engineInitialized=true;}
    $('connection').textContent = s.worker_error ? 'Worker needs attention' : s.paused ? 'Paused' : 'Listening';
    paused=s.paused; $('pause').textContent=paused?'Resume':'Pause'; $('taskCount').textContent=s.tasks.length;
    $('limits').textContent=`${s.limits.max_runs_per_day} runs/day · ${s.limits.timeout_seconds / 60} min/run`;
    $('connections').replaceChildren(...Object.entries(s.integrations||{}).map(([name, value])=>{
      const label={computer:'Computer use',slack:'Slack event listener'}[name]||name;
      const box=el('div',undefined,'integration');box.append(el('strong',`${label}: ${value.status.replaceAll('_',' ')}`),el('p',value.detail));
      if(value.checked_at)box.append(el('small',`Checked ${new Date(value.checked_at*1000).toLocaleTimeString()}`));return box;
    }));
    $('memories').replaceChildren(...(s.memories||[]).map(m=>{const row=el('div',undefined,'card');row.append(el('strong',m.scope),el('p',m.text),button('Retire instruction',()=>api('/api/memory',{action:'retire',id:m.id})));return row;}));
    const pendingTools=s.tool_approvals||[];
    const decisionCount=s.approvals.length+pendingTools.length;
    $('approvalCount').textContent=decisionCount ? `(${decisionCount})` : '';
    const existing = new Map([...$('approvals').querySelectorAll('textarea')].map(t=>[t.dataset.id,t.value]));
    $('approvals').replaceChildren(...s.approvals.map(a=>{
      const card=el('div',undefined,'card approval');card.append(el('h3','Task decision needed'),el('p',a.question),el('small','Your answer resumes the task; it does not grant tool or app permissions.'));
      const note=el('textarea');note.placeholder='Your answer or instructions for continuing';note.dataset.id=a.id;note.value=existing.get(a.id)||'';
      const actions=el('div',undefined,'actions');
      for(const d of ['approve','deny']) actions.append(button(d==='approve'?'Approve and continue':'Decline',()=>api('/api/decision',{id:a.id,scope_hash:a.scope_hash,decision:d,note:note.value})));
      card.append(note,actions,el('div',`Task ${a.task_id.slice(0,8)} · revision ${a.revision} · expires ${new Date(a.expires*1000).toLocaleString()}`,'meta'));return card;
    }));
    for(const a of pendingTools){
      const card=el('div',undefined,'card approval');
      card.append(el('h3','Live computer-use permission'),el('p',a.request.message),el('small','This answers the tool request in the running worker. It does not change global permissions.'));
      const details=el('details');details.append(el('summary','Inspect permission scope'),el('pre',JSON.stringify(a.request,null,2)));card.append(details);
      const actions=el('div',undefined,'actions');
      for(const d of ['accept','decline'])actions.append(button(d==='accept'?'Allow this request':'Decline request',()=>api('/api/tool-decision',{id:a.id,scope_hash:a.scope_hash,decision:d})));
      card.append(actions,el('div',`Task ${a.task_id.slice(0,8)} · expires ${new Date(a.expires*1000).toLocaleTimeString()}`,'meta'));
      $('approvals').prepend(card);
    }
    if(!decisionCount)$('approvals').append(el('div','No decisions waiting for you.','empty'));
    $('tasks').replaceChildren(...s.tasks.map(t=>{
      const card=el('article',undefined,'card');const top=el('div',undefined,'top');top.append(el('h3',t.title),el('span',t.state.replaceAll('_',' '),'badge '+t.state));card.append(top);
      if(t.result) card.append(el('p',(['queued','running'].includes(t.state)?'Previous attempt: ':'')+t.result.summary));
      if(t.result?.artifacts?.length){const details=el('details');details.dataset.key=t.id+':artifacts';details.open=opened.has(details.dataset.key);details.append(el('summary',`${t.result.artifacts.length} local artifacts`));for(const a of t.result.artifacts)details.append(button(`Read ${a.path}`,async()=>{const v=await api(`/api/artifact?task=${encodeURIComponent(t.id)}&path=${encodeURIComponent(a.path)}`);$('artifactTitle').textContent=v.path;$('artifactText').textContent=v.text;$('artifact').showModal();}));card.append(details);}
      card.append(el('div',`${t.engine} · ${t.id.slice(0,8)} · attempt ${t.attempts} · ${new Date(t.updated*1000).toLocaleString()}`,'meta'));
      if(t.result?.workspace)card.append(el('div',t.result.workspace,'meta'));
      const actions=el('div',undefined,'actions');
      if(t.state==='ready_for_review')actions.append(button('Mark reviewed',()=>api('/api/task-control',{id:t.id,action:'complete'})));
      if(['failed','blocked','interrupted'].includes(t.state))actions.append(button('Retry after inspection',()=>api('/api/task-control',{id:t.id,action:'retry'})));
      if(['queued','running','waiting_approval'].includes(t.state))actions.append(button('Cancel',()=>api('/api/task-control',{id:t.id,action:'cancel'})));
      if(t.state!=='cancelled'){
        const feedback=el('details');feedback.dataset.key=t.id+':feedback';feedback.open=opened.has(feedback.dataset.key);feedback.append(el('summary','Correct or redirect this task'));
        const note=el('textarea');note.dataset.task=t.id;note.value=drafts.get(t.id)||'';note.placeholder='What should Saha change?';note.setAttribute('aria-label',`Feedback for ${t.title}`);
        feedback.append(note,button('Apply feedback and continue',()=>api('/api/feedback',{id:t.id,text:note.value})));card.append(feedback);
      }
      card.append(actions);return card;
    }));
    if(!s.tasks.length)$('tasks').append(el('div','No work yet. Submit a job above or connect an event source.','empty'));
    $('error').hidden = true;
    $('receipts').replaceChildren(...s.receipts.slice(0,15).map(r=>el('div',`${new Date(r.at*1000).toLocaleTimeString()} · ${r.kind.replaceAll('_',' ')} · ${(r.task_id||'runtime').slice(0,8)}`,'activity')));
  } catch(e){fail(e);$('connection').textContent='Disconnected';}finally{busy=false;}
}
$('submit').onsubmit=async e=>{e.preventDefault();const b=e.submitter;b.disabled=true;try{await api('/api/tasks',{id:crypto.randomUUID(),title:$('title').value,text:$('request').value,engine:$('engine').value});$('title').value='';$('request').value='';await refresh();}catch(e){fail(e);}finally{b.disabled=false;}};
$('pause').onclick=async()=>{try{await api('/api/control',{action:paused?'resume':'pause'});await refresh();}catch(e){fail(e);}};
$('memoryForm').onsubmit=async e=>{e.preventDefault();try{await api('/api/memory',{scope:$('memoryScope').value,text:$('memoryText').value});$('memoryText').value='';await refresh();}catch(e){fail(e);}};
$('closeArtifact').onclick=()=>$('artifact').close();
$('speakArtifact').onclick=()=>{if(!window.speechSynthesis){fail(new Error('Read aloud is unavailable in this browser'));return;}speechSynthesis.cancel();speechSynthesis.speak(new SpeechSynthesisUtterance($('artifactText').textContent.slice(0,12000)));};
const SpeechInput=window.SpeechRecognition||window.webkitSpeechRecognition;
$('dictate').disabled=!SpeechInput;
$('voiceStatus').textContent=SpeechInput?'Optional dictation uses your browser’s speech service. Review the transcript before starting work.':'Voice dictation is unavailable in this browser. You can use OpenWhispr to dictate into the task field.';
$('dictate').onclick=()=>{const recognition=new SpeechInput();recognition.lang='en-IN';recognition.interimResults=false;recognition.onresult=e=>{$('request').value+=($('request').value?'\n':'')+e.results[0][0].transcript;$('voiceStatus').textContent='Transcript added. Review it, then start work.';};recognition.onerror=e=>{$('voiceStatus').textContent='Dictation unavailable: '+e.error;};recognition.onend=()=>{$('dictate').disabled=false;};$('dictate').disabled=true;try{recognition.start();$('voiceStatus').textContent='Listening for your task…';}catch(e){$('dictate').disabled=false;fail(e);}};
if(!token){$('login').hidden=false;$('pause').hidden=true;$('connection').textContent='Local dashboard';}else{refresh();setInterval(()=>{if(!document.querySelector('textarea:focus'))refresh();},3000);}
