'use strict';
(() => {
  const $ = (id) => document.getElementById(id);
  const svg = $('evidence-graph'), nodeLayer = $('graph-nodes'), edgeLayer = $('graph-edges');
  const positions = new Map(), hints = [], hintEdges = [];
  let key = '', timer = null, inFlight = false, generation = 0, nodes = [], edges = [], selected = null;
  let linking = false, linkSource = null, drag = null, context = null, graphHeight = 640;
  let chosenDecision=null;
  const undoStack=[], redoStack=[];
  const sessionName='dwellmind.readonly.v1', mapName='dwellmind.map.v1';
  function stored(name) {try{return localStorage.getItem(name);}catch{return null;}}
  function storeValue(name,value) {try{value===null?localStorage.removeItem(name):localStorage.setItem(name,value);return true;}catch{return false;}}
  function snapshot(){return JSON.stringify({hints,hintEdges,positions:[...positions],note:$('hypothesis-note').value});}
  function checkpoint(){undoStack.push(snapshot());if(undoStack.length>50)undoStack.shift();redoStack.length=0;}
  function restore(raw){const d=JSON.parse(raw);hints.splice(0,hints.length,...d.hints);hintEdges.splice(0,hintEdges.length,...d.hintEdges);positions.clear();for(const [id,p] of d.positions)positions.set(id,p);$('hypothesis-note').value=d.note;cancelLink();if(context)render(context);}
  function editDone(){if(context)render(context);$('undo').disabled=!undoStack.length;$('redo').disabled=!redoStack.length;if($('remember-map').checked)storeValue(mapName,snapshot());}
  function cancelLink(){linking=false;linkSource=null;$('connect-nodes').classList.remove('active');$('connect-nodes').textContent='Connect nodes';$('map-help').textContent='Select an idea to edit it or remove its connections. Escape cancels linking.';}
  const actorNames = {automation:'Automation',script:'Script',engine:'Engine',user_associated:'HA user context · intent unproven',unattributed:'Unknown source'};
  const colors = {entity:'observed',collector:'observed',model:'learned',evaluation:'learned',hypothesis:'hint',control:'muted'};
  const format = (value) => typeof value === 'number' ? value.toLocaleString(undefined,{maximumFractionDigits:3}) : '—';
  const label = (id) => id.split('.').pop().replaceAll('_',' ');
  const short = (value, max=23) => value.length > max ? value.slice(0,max-1)+'…' : value;
  function element(tag, attrs={}, text=null) {
    const e = document.createElementNS('http://www.w3.org/2000/svg',tag);
    for (const [name,value] of Object.entries(attrs)) e.setAttribute(name,String(value));
    if (text !== null) e.textContent = String(text);
    return e;
  }
  async function read(path, activeKey) {
    const response = await fetch(path,{method:'GET',headers:{Authorization:'Bearer '+activeKey},cache:'no-store',credentials:'omit',mode:'same-origin',signal:AbortSignal.timeout(7000)});
    if (response.status === 401) throw new Error('Pairing key rejected.');
    if (!response.ok) throw new Error('Worker data is unavailable. The last displayed data may be stale.');
    const raw = await response.text();
    if (raw.length > 131072) throw new Error('Worker response exceeds the dashboard limit.');
    const data = JSON.parse(raw);
    if (data.protocol !== 1 || data.control_enabled !== false) throw new Error('Unsupported worker response.');
    return data;
  }
  function connection(text, online=false) {
    $('connection-status').textContent = text;
    $('connection-status').className = 'pill'+(online?' online':'');
  }
  async function poll() {
    if (!key || inFlight) return;
    inFlight = true;
    const activeKey = key, revision = generation;
    try {
      const data = await read('/v1/context',activeKey);
      if (generation !== revision) return;
      context = data;
      render(data);
      showForecast(data.forecast);
      showShadow(data);
      connection(data.status.test_fixture?'Demo fixture · not household data':'Live · shadow mode',true);
      $('pair-panel').hidden = true;
      $('disconnect').hidden = false;
      $('pair-key').value = '';
      $('pair-error').textContent = '';
      $('last-update').textContent = 'Updated '+new Date().toLocaleTimeString();
    } catch (error) {
      if (generation !== revision) return;
      connection(context?'Connection lost · data stale':'Not connected');
      $('overview-title').textContent='Connection unavailable';
      $('overview-message').textContent='The worker cannot be reached. Any previously displayed results may be out of date.';
      $('overview-action').disabled=true;
      $('pair-error').textContent = error instanceof SyntaxError ? 'Invalid worker response.' : error.message;
      if (!context) $('pair-panel').hidden = false;
    } finally { inFlight = false; }
  }
  function render(data) {
    showOverview(data);
    const status = data.status, result = status.learning_summary;
    $('worker-state').textContent = data.scope.length ? status.state : 'Paused';
    $('scope-count').textContent = status.room_count+' rooms · '+status.entity_count+' selected entities';
    $('row-count').textContent = format(status.rows);
    $('gap-count').textContent = status.gaps+' capture boundaries / interruptions';
    $('history-count').textContent = data.shadow?format(data.shadow.snapshot_examples):'—';
    $('move-boundary').textContent = data.shadow?'Old and current homes evaluated separately':'Update worker for campaign learning';
    $('gain').textContent = data.shadow?Object.values(data.shadow.reports).filter(r=>r.state==='beats_baselines').length:'—';
    nodes = [];
    for (const room of data.scope) for (const id of room.entities) {
      const observation = data.observations[id];
      const reported = observation && observation.availability === 'reported';
      const previous = context && document.querySelector('[data-node-id="'+CSS.escape(id)+'"]');
      const stamp = observation ? observation.time : null;
      nodes.push({id,kind:'entity',title:label(id),meta:reported?(observation.state==='numeric'?format(observation.value)+' '+(observation.unit||''):observation.state):'Unknown / no fresh observation',
        detail:'An observed input, not a verified activity or preference.',changed:!!(previous && previous.getAttribute('data-stamp')!==stamp),
        stamp,fields:{Entity:id,Room:room.name,Availability:observation?observation.availability:'unknown',Source:observation?actorNames[observation.actor]||'Unknown source':'unknown',
          Record:observation?observation.record_kind:'none',Observed:stamp?new Date(stamp).toLocaleString():'not observed'}});
    }
    nodes.push({id:'@collector',kind:'collector',title:'Live observation',meta:format(status.rows)+' projected records',detail:'Only the reviewed, permitted scope is collected. Gaps and snapshots are not actions.',fields:{Rooms:status.room_count,Entities:status.entity_count,Gaps:status.gaps,Control:'disabled'}});
    nodes.push({id:'@control',kind:'control',title:'Device control',meta:'Disabled · not implemented',detail:'The execution broker does not exist yet. There are no device-control buttons in this dashboard.',fields:{Status:'disabled',Mode:'observation only'}});
    edges = nodes.filter(n=>n.kind==='entity').map(n=>({from:n.id,to:'@collector',kind:'observed',human:false}));
    if (result && data.active_model_available) {
      nodes.push({id:'@history',kind:'model',title:'Historical dataset',meta:format(result.coverage.source_rows)+' hourly records',detail:'Historical temperature statistics, separated at the move boundary. This is not a bathroom activity dataset.',fields:{Start:result.source_start,End:result.source_end,Missing:result.coverage.missing_hour_slots,Unit:result.unit}});
      nodes.push({id:'@model',kind:'model',title:'Learned temperature',meta:result.selected_on_validation.replaceAll('_',' '),detail:'Predicts the next hourly averaged temperature. Candidate selection used validation data, not the final test.',fields:{Task:result.task,Target:result.entity_id,Selected:result.selected_on_validation,Trained:result.trained_at}});
      nodes.push({id:'@evaluation',kind:'evaluation',title:'Held-out evaluation',meta:format(result.mae_improvement_over_best_baseline_percent)+'% MAE improvement',detail:'Measured on an untouched later period. A forecast gain does not prove an appropriate device action.',fields:{TestStart:result.test_start,Examples:result.held_out_test.learned.examples,MAE:format(result.held_out_test.learned.mae)+' '+result.unit,Outcome:result.evaluation_status}});
      if (nodes.some(n=>n.id===result.entity_id)) edges.push({from:result.entity_id,to:'@history',kind:'source',human:false});
      edges.push({from:'@history',to:'@model',kind:'trained',human:false},{from:'@model',to:'@evaluation',kind:'evaluated',human:false});
    }
    nodes.push(...hints);
    const decision=data.shadow?.decisions.find(d=>d.decision_id===chosenDecision);
    if(decision){
      nodes.push({id:'@shadow-root',kind:'model',title:label(decision.target)+' → '+decision.predicted,meta:format(decision.probability*100)+'% · '+decision.model_state,
        detail:'A learned five-minute behavior forecast. Supporting associations do not establish causation or preference.',fields:{Target:decision.target,Channel:decision.channel,Window:new Date(decision.target_ms).toLocaleString(),Boundaries:decision.blocked_by.join(', ')}});
      const sources=new Set(decision.evidence.filter(e=>!e.feature.startsWith('@')).map(e=>e.feature.split('|')[0]));
      for(const source of sources)if(nodes.some(n=>n.id===source))edges.push({from:source,to:'@shadow-root',kind:'learned',human:false});
      edges.push({from:'@shadow-root',to:'@control',kind:'blocked',human:false});
    }
    const valid = new Set(nodes.map(n=>n.id));
    edges.push(...hintEdges.filter(e=>valid.has(e.from)&&valid.has(e.to)));
    layout();
    draw();
    showModel(result,data.active_model_available);
    $('graph-empty').hidden = true;
    for (const id of ['connect-nodes','reset-layout','add-hypothesis','export-map']) $(id).disabled = false;
    if (selected) inspect(nodes.find(n=>n.id===selected));
    $('undo').disabled=!undoStack.length;$('redo').disabled=!redoStack.length;
    const source=$('context-source'),previous=source.value;source.textContent='';
    for(const n of nodes.filter(n=>n.kind==='entity')){const o=document.createElement('option');o.value=n.id;o.textContent=n.title+' · '+n.meta;source.append(o);}source.value=previous||source.options[0]?.value||'';
  }
  function layout() {
    const fixed = {'@collector':[565,280],'@history':[825,90],'@model':[825,275],'@evaluation':[825,450],'@control':[565,535],'@shadow-root':[565,100]};
    const entities = nodes.filter(n=>n.kind==='entity');
    const inputHeight=Math.max(640,Math.ceil(entities.length/2)*82+95);
    graphHeight=Math.max(inputHeight,hints.length?inputHeight+Math.ceil(hints.length/3)*85+40:640);
    svg.setAttribute('viewBox','0 0 1000 '+graphHeight);
    entities.forEach((n,i)=>{
      if (!positions.has(n.id)) {
        positions.set(n.id,[135+(i%2)*205,70+Math.floor(i/2)*82]);
      }
    });
    nodes.filter(n=>n.kind!=='entity').forEach((n,i)=>{
      if (!positions.has(n.id)) {
        const hintIndex=hints.findIndex(h=>h.id===n.id);
        positions.set(n.id,fixed[n.id]||[150+(hintIndex%3)*330,inputHeight+40+Math.floor(hintIndex/3)*85]);
      }
    });
  }
  function draw() {
    nodeLayer.textContent = ''; edgeLayer.textContent = '';
    for (const e of edges) {
      const a = positions.get(e.from), b = positions.get(e.to);
      if (!a||!b) continue;
      edgeLayer.append(element('path',{d:'M '+a[0]+' '+a[1]+' Q '+((a[0]+b[0])/2+(e.kind==='blocked'?180:0))+' '+((a[1]+b[1])/2-25)+' '+b[0]+' '+b[1],class:'edge '+(e.human?'human ':'')+e.kind}));
    }
    for (const n of nodes) {
      const p = positions.get(n.id);
      const g = element('g',{transform:'translate('+p[0]+','+p[1]+')',class:'node '+n.kind+(n.changed?' changed':'')+(selected===n.id?' selected':''),tabindex:0,role:'button','aria-label':n.title,'data-node-id':n.id,'data-stamp':n.stamp||''});
      g.append(element('rect',{x:-91,y:-32,width:182,height:64,rx:12}),element('circle',{cx:-76,cy:-10,r:3,class:'marker'}),
        element('text',{x:-64,y:-6},short(n.title)),element('text',{x:-76,y:16,class:'meta'},short(n.meta,31)));
      g.addEventListener('pointerdown',event=>{const point=location(event);drag={id:n.id,start:point,origin:p.slice(),moved:false};svg.setPointerCapture(event.pointerId);});
      g.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();choose(n.id);}});
      nodeLayer.append(g);
    }
  }
  function location(event) {
    const p=svg.createSVGPoint();p.x=event.clientX;p.y=event.clientY;
    const local=p.matrixTransform(svg.getScreenCTM().inverse());return [local.x,local.y];
  }
  svg.addEventListener('pointermove',event=>{
    if (!drag) return;
    const p=location(event),dx=p[0]-drag.start[0],dy=p[1]-drag.start[1];
    if(Math.abs(dx)+Math.abs(dy)>5)drag.moved=true;
    positions.set(drag.id,[Math.max(95,Math.min(905,drag.origin[0]+dx)),Math.max(40,Math.min(graphHeight-40,drag.origin[1]+dy))]);draw();
  });
  svg.addEventListener('pointerup',()=>{if(!drag)return;const d=drag;drag=null;if(!d.moved)choose(d.id);else{const destination=positions.get(d.id);positions.set(d.id,d.origin);checkpoint();positions.set(d.id,destination);editDone();}});
  svg.addEventListener('pointercancel',()=>{drag=null;});
  function choose(id) {
    selected=id;
    if(linking) {
      if(!linkSource) {linkSource=id;$('connect-nodes').textContent='Choose target node';}
      else if(linkSource!==id) {
        if(hintEdges.length<128&&!hintEdges.some(e=>e.from===linkSource&&e.to===id&&e.kind===$('edge-kind').value)) {
          checkpoint();
          hintEdges.push({from:linkSource,to:id,kind:$('edge-kind').value,human:true});
        }
        cancelLink();editDone();
      }
    }
    inspect(nodes.find(n=>n.id===id));draw();
  }
  function inspect(n) {
    if(!n) {selected=null;$('node-title').textContent='Choose a node';$('node-detail').textContent='This selection is no longer in the current evidence view.';$('node-fields').textContent='';$('connection-list').textContent='';return;}
    $('node-title').textContent=n.title;$('node-detail').textContent=n.detail;$('node-fields').textContent='';
    for(const [name,value] of Object.entries(n.fields||{})) {const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=name;dd.textContent=String(value);$('node-fields').append(dt,dd);}
    $('delete-idea').disabled=n.kind!=='hypothesis';$('rename-idea').disabled=n.kind!=='hypothesis';
    $('idea-name').value=n.kind==='hypothesis'?n.title:'';
    $('connection-list').textContent='';
    hintEdges.forEach((e,i)=>{if(e.from!==n.id&&e.to!==n.id)return;const b=document.createElement('button');b.className='quiet connection-remove';b.textContent='Remove: '+(nodes.find(x=>x.id===e.from)?.title||e.from)+' → '+(nodes.find(x=>x.id===e.to)?.title||e.to)+' ('+e.kind+')';b.addEventListener('click',()=>{checkpoint();hintEdges.splice(i,1);editDone();});$('connection-list').append(b);});
  }
  function showModel(result,active) {
    $('model-bars').textContent='';
    if(!result) {$('model-task').textContent='No model result loaded.';$('model-status').textContent='Not trained';return;}
    $('model-task').textContent='Next-hour mean temperature · '+result.selected_on_validation.replaceAll('_',' ')+' · '+result.held_out_test.learned.examples+' held-out examples';
    $('model-status').textContent=active?result.evaluation_status.replaceAll('_',' '):'Historical result · outside current scope';
    const entries=[['persistence','Temperature stays the same'],['previous_day','Repeat yesterday'],['learned','Learned forecast']];
    const max=Math.max(...entries.map(([id])=>result.held_out_test[id].mae),.001);
    for(const [id,name] of entries) {
      const value=result.held_out_test[id].mae,row=document.createElement('div'),title=document.createElement('span'),track=document.createElement('div'),fill=document.createElement('div'),score=document.createElement('strong');
      row.className='bar-row '+id;title.textContent=name;track.className='bar-track';fill.className='bar-fill';fill.style.width=(100*value/max)+'%';score.textContent=format(value)+' '+result.unit;
      track.append(fill);row.append(title,track,score);$('model-bars').append(row);
    }
    $('model-limits').textContent=(result.limitations||[]).join(' ');
  }
  function startPolling(){generation++;context=null;poll();if(timer)clearInterval(timer);timer=setInterval(poll,5000);}
  $('pair-form').addEventListener('submit',async event=>{event.preventDefault();key=$('pair-key').value.trim();if($('remember-access').checked){try{const session=await read('/v1/ui-workspace-session',key);key=session.credential;if(!storeValue(sessionName,key))$('pair-error').textContent='Browser storage unavailable; access lasts for this tab.';}catch(error){$('pair-error').textContent=error.message;return;}}else storeValue(sessionName,null);startPolling();});
  $('disconnect').addEventListener('click',()=>{key='';generation++;clearInterval(timer);timer=null;context=null;nodes=[];edges=[];draw();$('pair-panel').hidden=false;$('disconnect').hidden=true;$('graph-empty').hidden=false;$('pair-key').value='';connection('Disconnected');$('last-update').textContent='No live data loaded';
    for(const id of ['ability-cards','decision-feed','room-overview','overview-progress'])$(id).textContent='';
    $('overview-title').textContent='Connect to see progress';$('overview-message').textContent='No worker data loaded.';
    $('overview-next').textContent='Connect privately to view the worker.';$('overview-action').disabled=true;
    $('context-source').textContent='';
    for(const id of ['shadow-models','shadow-issued','shadow-evaluated','shadow-unknown'])$(id).textContent='0';
    $('shadow-status').textContent='Disconnected';$('shadow-progress').textContent='Connect to see campaign progress.';$('archive-progress').textContent='No worker data loaded.';
    $('archive-password').value='';$('archive-user').value='';$('archive-url').value='';$('campaign-error').textContent='';
    $('archive-legacy').checked=false;$('import-explanation').textContent='Connect to view history import progress.';
    for(const id of ['start-campaign','stop-campaign','import-archive','delete-idea','rename-idea'])$(id).disabled=true;
    storeValue(sessionName,null);cancelLink();$('forecast-chart').textContent='';$('forecast-chart').hidden=true;$('forecast-value').textContent='—';$('forecast-detail').textContent='Connect to see forecasts.';$('forecast-contributions').textContent='';
    for(const id of ['worker-state','row-count','history-count','gain'])$(id).textContent='—';
    $('scope-count').textContent='Awaiting connection';$('gap-count').textContent='Gaps remain unknown';$('move-boundary').textContent='Move-aware evaluation';
    $('node-fields').textContent='';$('node-title').textContent='Choose a node';$('node-detail').textContent='Connect to inspect evidence.';
    $('model-bars').textContent='';$('model-task').textContent='No model result loaded.';$('model-status').textContent='Awaiting data';$('model-limits').textContent='No data loaded.';
    for(const id of ['connect-nodes','reset-layout','add-hypothesis'])$(id).disabled=true;
  });
  $('connect-nodes').addEventListener('click',()=>{if(linking){cancelLink();return;}linking=true;linkSource=null;$('connect-nodes').classList.add('active');$('connect-nodes').textContent='Cancel connection';$('map-help').textContent='Choose a source node, then a target. Escape cancels.';});
  $('reset-layout').addEventListener('click',()=>{checkpoint();positions.clear();layout();editDone();});
  $('hypothesis-form').addEventListener('submit',event=>{
    event.preventDefault();if(!context||hints.length>=24)return;
    const title=$('hypothesis-label').value.trim();if(!title)return;
    const identity=Array.from(crypto.getRandomValues(new Uint8Array(12)),v=>v.toString(16).padStart(2,'0')).join('');
    checkpoint();
    hints.push({id:'@hint-'+identity,kind:'hypothesis',title,meta:'Human idea · not evaluated',detail:'User-supplied hypothesis. It has not been tested or applied to any model.',fields:{Source:'human hypothesis',Status:'not evaluated',Control:'none'}});
    const source=$('context-source').value;
    if(source)hintEdges.push({from:source,to:hints.at(-1).id,kind:$('context-role').value,human:true});
    $('hypothesis-label').value='';editDone();choose(hints.at(-1).id);
  });
  $('export-map').addEventListener('click',()=>{
    const data={schema:1,purpose:'human_hypotheses_only',applied_to_models:false,requires_review:true,
      nodes:hints.map(n=>({id:n.id,label:n.title})),edges:hintEdges.map(e=>({from:e.from,to:e.to,relationship:e.kind})),note:$('hypothesis-note').value.slice(0,500)};
    const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
    const a=document.createElement('a');a.href=url;a.download='dwellmind-context-hypotheses.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  $('undo').addEventListener('click',()=>{if(!undoStack.length)return;redoStack.push(snapshot());restore(undoStack.pop());editDone();});
  $('redo').addEventListener('click',()=>{if(!redoStack.length)return;undoStack.push(snapshot());restore(redoStack.pop());editDone();});
  document.addEventListener('keydown',e=>{if(e.key==='Escape')cancelLink();if(e.target.matches('input,textarea,select'))return;if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();$(e.shiftKey?'redo':'undo').click();}});
  $('delete-idea').addEventListener('click',()=>{const i=hints.findIndex(n=>n.id===selected);if(i<0)return;checkpoint();hints.splice(i,1);for(let j=hintEdges.length-1;j>=0;j--)if(hintEdges[j].from===selected||hintEdges[j].to===selected)hintEdges.splice(j,1);selected=null;editDone();$('node-title').textContent='Idea removed · Undo to restore';$('node-fields').textContent='';$('connection-list').textContent='';$('delete-idea').disabled=$('rename-idea').disabled=true;});
  $('rename-idea').addEventListener('click',()=>{const n=hints.find(n=>n.id===selected),title=$('idea-name').value.trim();if(!n||!title)return;checkpoint();n.title=title;editDone();});
  $('remember-map').addEventListener('change',()=>storeValue(mapName,$('remember-map').checked?snapshot():null));
  let noteBefore='';$('hypothesis-note').addEventListener('focus',()=>{noteBefore=snapshot();});$('hypothesis-note').addEventListener('change',()=>{undoStack.push(noteBefore);redoStack.length=0;editDone();});
  function showForecast(f){
    $('forecast-contributions').textContent='';$('forecast-chart').textContent='';$('forecast-chart').setAttribute('hidden','');
    $('forecast-value').textContent=f?.state==='forecast'?format(f.predicted_mean)+' '+f.unit:'—';
    $('forecast-status').textContent=f?.state||'waiting';
    if(!f||f.state==='waiting'){$('forecast-detail').textContent=f?.reason||'Update the worker and Home Assistant companion to enable ongoing forecasts.';return;}
    $('forecast-detail').textContent=(f.state==='stale'?'Expired forecast · ':f.state==='abstained'?'Model abstained · ':'')+label(f.entity_id)+' · '+new Date(f.target_start).toLocaleString()+' – '+new Date(f.target_end).toLocaleTimeString()+'. Persistence: '+format(f.persistence_mean)+' '+f.unit+'. Historical average error: '+format(f.held_out_mae)+' '+f.unit+' (not a confidence interval).';
    if(f.state!=='forecast')return;
    if(f.observed_hours?.length){
      const chart=$('forecast-chart'),values=f.observed_hours.map(r=>r.mean),lo=Math.min(...values,f.predicted_mean)-.2,hi=Math.max(...values,f.predicted_mean)+.2;
      const y=v=>150-(v-lo)/(hi-lo)*120,x=i=>45+i*24;
      chart.removeAttribute('hidden');
      chart.append(element('polyline',{points:values.map((v,i)=>x(i)+','+y(v)).join(' '),fill:'none',stroke:'#54e0d2','stroke-width':3}));
      chart.append(element('line',{x1:x(values.length-1),y1:y(values.at(-1)),x2:x(values.length),y2:y(f.predicted_mean),stroke:'#ac91ff','stroke-width':3,'stroke-dasharray':'5 4'}));
      chart.append(element('circle',{cx:x(values.length),cy:y(f.predicted_mean),r:5,fill:'#ac91ff'}));
      chart.append(element('text',{x:45,y:180,fill:'#a6b5ca','font-size':12},'Last 25 completed hourly means'));
      chart.append(element('text',{x:510,y:180,fill:'#ac91ff','font-size':12},'Next hour forecast'));
      chart.append(element('text',{x:4,y:30,fill:'#a6b5ca','font-size':11},format(hi)));
      chart.append(element('text',{x:4,y:150,fill:'#a6b5ca','font-size':11},format(lo)));
    }

    const root=document.createElement('div');root.className='reason-root';root.textContent='Predicted hourly mean: '+format(f.predicted_mean)+' '+f.unit;$('forecast-contributions').append(root);
    const branches=document.createElement('div');branches.className='reason-branches';
    for(const c of f.contributions){const branch=document.createElement('div');branch.className='reason-leaf';branch.textContent=c.feature.replaceAll('_',' ')+' → '+(c.change>=0?'+':'')+format(c.change)+' '+f.unit;branches.append(branch);}$('forecast-contributions').append(branches);
  }
  const savedMap=stored(mapName);if(savedMap){try{const d=JSON.parse(savedMap);if(d.hints.length<=24&&d.hintEdges.length<=128){restore(savedMap);$('remember-map').checked=true;}}catch{storeValue(mapName,null);}}
  async function write(path,payload){
    if(!key||key.startsWith('ui.'))throw new Error('Pair once with the updated worker to authorize shadow jobs on this browser.');
    const response=await fetch(path,{method:'POST',headers:{Authorization:'Bearer '+key,'Content-Type':'application/json'},body:JSON.stringify(payload),credentials:'omit',mode:'same-origin',signal:AbortSignal.timeout(15000)});
    if(!response.ok)throw new Error(response.status===401?'Workspace credential expired. Reconnect privately.':'Job refused. Check the reviewed scope, active campaign, connection fields and worker update.');
    return response.json();
  }
  $('campaign-zone').value=Intl.DateTimeFormat().resolvedOptions().timeZone;
  const archiveStart=new Date();archiveStart.setUTCFullYear(archiveStart.getUTCFullYear()-3);$('archive-start').value=archiveStart.toISOString().slice(0,10);
  $('campaign-form').addEventListener('submit',async e=>{e.preventDefault();$('start-campaign').disabled=true;try{await write('/v1/shadow/start',{days:Number($('campaign-days').value),move_date:$('campaign-move').value,timezone:$('campaign-zone').value});$('campaign-error').textContent='';await poll();}catch(error){$('campaign-error').textContent=error.message;}finally{$('start-campaign').disabled=false;}});
  $('stop-campaign').addEventListener('click',async()=>{try{await write('/v1/shadow/stop',{});await poll();}catch(error){$('campaign-error').textContent=error.message;}});
  $('archive-form').addEventListener('submit',async e=>{e.preventDefault();$('import-archive').disabled=true;try{await write('/v1/shadow/archive',{url:$('archive-url').value.trim(),database:$('archive-db').value.trim(),username:$('archive-user').value,password:$('archive-password').value,start:$('archive-start').value+'T00:00:00Z',reuse_legacy:$('archive-legacy').checked});$('archive-password').value='';$('campaign-error').textContent='';await poll();}catch(error){$('campaign-error').textContent=error.message;}finally{$('import-archive').disabled=false;}});
  function setting(d,value){const n=Number(value);if(!Number.isFinite(n))return value;if(d.channel==='brightness')return format(n*100/255)+'%';if(d.channel==='color_temp_kelvin')return format(n)+' K';if(d.channel==='temperature')return format(n)+' '+(d.unit||'(unit unverified)');if(['current_position','percentage'].includes(d.channel))return format(n)+'%';return value;}
  function showOverview(data){
    const view=DwellMindOverview(data);
    $('overview-title').textContent=view.title;$('overview-message').textContent=view.message;
    $('overview-progress').textContent=view.progress;$('overview-next').textContent=view.next;
    $('overview-action').textContent=view.action;$('overview-action').dataset.destination=view.destination;
    $('overview-action').disabled=false;$('room-overview').textContent='';
    for(const room of view.rooms){
      const card=document.createElement('article');card.className='panel room-card';
      const title=document.createElement('h2'),body=document.createElement('p'),availability=document.createElement('small');
      title.textContent=room.name;body.textContent=room.devices+' selected items · '+room.targets+' types of forecast being tested';
      availability.textContent=room.unavailable?room.unavailable+' devices have no usable current observation. History is kept.':'Selected devices are reporting data.';
      card.append(title,body,availability);$('room-overview').append(card);
    }
    $('import-explanation').textContent=view.importMessage+' Credentials are used for this import only; re-enter them after a worker restart.';
  }
  $('overview-action').addEventListener('click',()=>{
    const target=$($('overview-action').dataset.destination==='history'?'history-setup':'test-settings');
    target.open=true;target.scrollIntoView({block:'start'});
  });
  function showShadow(data){
    const s=data.shadow,a=data.archive;
    if(!s){$('shadow-progress').textContent='Update the worker and HA companion for the multi-target shadow campaign.';return;}
    $('history-count').textContent=format(s.snapshot_examples);$('move-boundary').textContent='Old and current homes evaluated separately';$('gain').textContent=Object.values(s.reports).filter(r=>r.state==='beats_baselines').length;
    $('shadow-status').textContent=s.training?'Training · capture continues':s.state.replaceAll('_',' ');
    for(const [id,value] of [['shadow-models',s.model_targets],['shadow-issued',s.predictions_issued],['shadow-evaluated',s.evaluated],['shadow-unknown',s.unknown_outcomes]])$(id).textContent=format(value);
    $('shadow-progress').textContent=s.error||(s.state==='running'?format(s.snapshot_examples)+' bounded training snapshots · retraining every six hours when sufficient data exists. ':'Campaign '+s.state.replaceAll('_',' ')+' · sources and models retained. ')+(s.evaluated?format(s.matched_reported_outcomes)+' matched reported outcomes. This is behavioral agreement, not preference validation.':'Outcomes are checked five minutes after each prediction; gaps remain unknown.')+' '+format(s.human_reviews||0)+' explicit reviews · '+format(s.preference_labels||0)+' desired-state labels.';
    if(!$('campaign-move').value&&data.status.learning_summary)$('campaign-move').value=data.status.learning_summary.coverage.move_boundary.slice(0,10);
    if(!$('campaign-move').value&&s.policy?.move_date)$('campaign-move').value=s.policy.move_date.slice(0,10);
    const canWrite=!!key&&!key.startsWith('ui.');$('start-campaign').disabled=!canWrite||s.state==='running';$('stop-campaign').disabled=!canWrite||s.state!=='running';$('import-archive').disabled=!canWrite||s.state!=='running'||a?.state==='importing';
    $('archive-progress').textContent=a?a.state.replaceAll('_',' ')+' · '+format(a.source_rows)+' new source rows · '+format(a.reused_source_rows||0)+' reused source rows · '+format(a.queries)+' bounded SELECT queries. '+(a.error_code?'Reason: '+a.error_code.replaceAll('_',' ')+'. ':'')+(a.error||'')+(a.training_error_code?' Training replay: '+a.training_error_code.replaceAll('_',' ')+'. Raw download progress preserved.':''):'';
    $('ability-cards').textContent='';
    const names={binary_sensor:'Occupancy / contact evidence',light:'Lighting states & settings',climate:'Climate modes & setpoints',cover:'Curtain states & positions',fan:'Ventilation behavior'};
    for(const [domain,name] of Object.entries(names)){
      const entities=data.scope.flatMap(r=>r.entities).filter(e=>e.startsWith(domain+'.'));
      const reports=Object.entries(s.reports).filter(([id])=>id.startsWith(domain+'.'));
      const card=document.createElement('article');card.className='ability-card';const title=document.createElement('strong'),body=document.createElement('p'),small=document.createElement('small');
      title.textContent=name;body.textContent=entities.length?entities.length+' reviewed entities · '+reports.filter(([,r])=>r.state==='beats_baselines').length+' targets beat held-out baselines':'Not enrolled · select devices through the HA integration.';
      small.textContent=reports.length?reports.map(([id,r])=>label(id.split('|')[0])+' / '+id.split('|')[1]+': '+r.state.replaceAll('_',' ')).join(' · '):entities.length?'Gathering variation and history; no trained model claimed.':'No collection or proposals for this capability.';
      card.append(title,body,small);$('ability-cards').append(card);
    }
    $('decision-feed').textContent='';
    if(!s.decisions.length){const p=document.createElement('p');p.textContent='No model predictions yet. Import history or continue collecting; constant/offline targets remain untrained.';$('decision-feed').append(p);}
    for(const d of s.decisions){
      const card=document.createElement('article');card.className='decision';const title=document.createElement('strong'),body=document.createElement('p'),details=document.createElement('details'),summary=document.createElement('summary'),why=document.createElement('p');
      const channelNames={state:'device state',brightness:'brightness',color_temp_kelvin:'light color',temperature:'temperature setting',current_position:'curtain position',percentage:'fan speed'};
      title.textContent=d.room+' · '+label(d.target);
      body.textContent='Forecast: '+channelNames[d.channel]+' '+setting(d,d.predicted)+' at '+new Date(d.target_ms).toLocaleTimeString()+'. '+(d.outcome_status==='matched reported outcome'?'Later observation agreed.':d.outcome_status==='different reported outcome'?'Later observation differed.':d.outcome_status==='unknown coverage'?'Could not check this forecast because observations were missing.':d.outcome_status||'Waiting for a later observation.')+' '+(d.model_state==='beats_baselines'?'Passed the historical comparison.':'Still being tested; not proven better than a simple forecast.');
      if(d.conditional_on)body.textContent='If the light is on: '+body.textContent;
      summary.textContent='Evidence and boundaries';why.textContent=d.evidence.map(v=>v.feature.replaceAll('|',' / ')+': '+v.value+' ('+format(v.log_support)+' relative log support)').join('; ')+'. Blocked: '+d.blocked_by.map(v=>v.replaceAll('_',' ')).join(', ')+(d.desired_action_learned?'. Explicit-review model available; device execution is disabled.':'. Behavioral forecast; desired preference is not established.');
      details.append(summary,why);card.append(title,body,details);
      if(d.preference_forecast){const p=document.createElement('p');p.textContent='Explicit-review preference model: '+setting(d,d.preference_forecast.predicted)+' · '+format(d.preference_forecast.probability*100)+'% · '+d.preference_forecast.state.replaceAll('_',' ')+'. Shadow only.';card.append(p);}
      const actions=document.createElement('div');actions.className='review-actions';
      const inspectButton=document.createElement('button');inspectButton.className='quiet';inspectButton.textContent='Why this forecast?';inspectButton.addEventListener('click',()=>{chosenDecision=d.decision_id;$('context-lab').open=true;render(context);choose('@shadow-root');$('evidence-graph').scrollIntoView({block:'center',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});});actions.append(inspectButton);
      if(!d.target.startsWith('binary_sensor.')){
      const question=document.createElement('p');question.textContent='Would you want this device '+(d.channel==='state'?'to be '+setting(d,d.predicted):'to use '+setting(d,d.predicted)+' for '+channelNames[d.channel])+' in this situation? '+(d.human_review?'Your answer: '+d.human_review:'Your answer teaches a preference; it will not change the device.');card.append(question);
      for(const [verdict,name] of [['appropriate','Yes, appropriate'],['inappropriate','No, unwanted'],['uncertain',d.human_review?'Undo review / unsure':'Unsure']]){const b=document.createElement('button');b.className='quiet';b.textContent=name;b.disabled=!canWrite||d.human_review===verdict;b.addEventListener('click',async()=>{b.disabled=true;try{await write('/v1/shadow/feedback',{decision_id:d.decision_id,verdict});await poll();}catch(error){$('campaign-error').textContent=error.message;b.disabled=false;}});actions.append(b);}
      }
      card.append(actions);$('decision-feed').append(card);
    }
  }
  const savedSession=stored(sessionName);if(savedSession?.startsWith('ui.')||savedSession?.startsWith('ui2.')){key=savedSession;$('remember-access').checked=true;startPolling();}
})();
