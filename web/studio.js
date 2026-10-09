'use strict';
(() => {
  const $ = (id) => document.getElementById(id);
  const svg = $('evidence-graph'), nodeLayer = $('graph-nodes'), edgeLayer = $('graph-edges');
  const positions = new Map(), hints = [], hintEdges = [];
  let key = '', timer = null, inFlight = false, generation = 0, nodes = [], edges = [], selected = null;
  let linking = false, linkSource = null, drag = null, context = null, graphHeight = 640;
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
      connection(data.status.test_fixture?'Demo fixture · not household data':'Live · read only',true);
      $('pair-panel').hidden = true;
      $('disconnect').hidden = false;
      $('pair-key').value = '';
      $('pair-error').textContent = '';
      $('last-update').textContent = 'Updated '+new Date().toLocaleTimeString();
    } catch (error) {
      if (generation !== revision) return;
      connection(context?'Connection lost · data stale':'Not connected');
      $('pair-error').textContent = error instanceof SyntaxError ? 'Invalid worker response.' : error.message;
      if (!context) $('pair-panel').hidden = false;
    } finally { inFlight = false; }
  }
  function render(data) {
    const status = data.status, result = status.learning_summary;
    $('worker-state').textContent = data.scope.length ? status.state : 'Paused';
    $('scope-count').textContent = status.room_count+' rooms · '+status.entity_count+' selected entities';
    $('row-count').textContent = format(status.rows);
    $('gap-count').textContent = status.gaps+' capture boundaries / interruptions';
    $('history-count').textContent = result ? format(result.coverage.source_rows) : '—';
    $('move-boundary').textContent = result ? 'Move boundary: '+result.coverage.move_boundary.slice(0,10) : 'No historical model yet';
    $('gain').textContent = result ? format(result.mae_improvement_over_best_baseline_percent)+'%' : '—';
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
    const valid = new Set(nodes.map(n=>n.id));
    edges.push(...hintEdges.filter(e=>valid.has(e.from)&&valid.has(e.to)));
    layout();
    draw();
    showModel(result,data.active_model_available);
    $('graph-empty').hidden = true;
    for (const id of ['connect-nodes','reset-layout','add-hypothesis','export-map']) $(id).disabled = false;
    if (selected) inspect(nodes.find(n=>n.id===selected));
  }
  function layout() {
    const fixed = {'@collector':[565,280],'@history':[825,90],'@model':[825,275],'@evaluation':[825,450],'@control':[565,535]};
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
      edgeLayer.append(element('path',{d:'M '+a[0]+' '+a[1]+' Q '+((a[0]+b[0])/2)+' '+((a[1]+b[1])/2-25)+' '+b[0]+' '+b[1],class:'edge '+(e.human?'human ':'')+e.kind}));
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
  svg.addEventListener('pointerup',()=>{if(!drag)return;const d=drag;drag=null;if(!d.moved)choose(d.id);});
  svg.addEventListener('pointercancel',()=>{drag=null;});
  function choose(id) {
    selected=id;
    if(linking) {
      if(!linkSource) {linkSource=id;$('connect-nodes').textContent='Choose target node';}
      else if(linkSource!==id) {
        if(hintEdges.length<128&&!hintEdges.some(e=>e.from===linkSource&&e.to===id&&e.kind===$('edge-kind').value))
          hintEdges.push({from:linkSource,to:id,kind:$('edge-kind').value,human:true});
        linkSource=null;linking=false;$('connect-nodes').classList.remove('active');$('connect-nodes').textContent='Connect nodes';
        edges.push(...hintEdges.slice(-1));draw();
      }
    }
    inspect(nodes.find(n=>n.id===id));draw();
  }
  function inspect(n) {
    if(!n) {selected=null;return;}
    $('node-title').textContent=n.title;$('node-detail').textContent=n.detail;$('node-fields').textContent='';
    for(const [name,value] of Object.entries(n.fields||{})) {const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=name;dd.textContent=String(value);$('node-fields').append(dt,dd);}
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
  $('pair-form').addEventListener('submit',event=>{event.preventDefault();key=$('pair-key').value.trim();generation++;context=null;poll();if(timer)clearInterval(timer);timer=setInterval(poll,5000);});
  $('disconnect').addEventListener('click',()=>{key='';generation++;clearInterval(timer);timer=null;context=null;nodes=[];edges=[];draw();$('pair-panel').hidden=false;$('disconnect').hidden=true;$('graph-empty').hidden=false;$('pair-key').value='';connection('Disconnected');$('last-update').textContent='No live data loaded';
    for(const id of ['worker-state','row-count','history-count','gain'])$(id).textContent='—';
    $('scope-count').textContent='Awaiting connection';$('gap-count').textContent='Gaps remain unknown';$('move-boundary').textContent='Move-aware evaluation';
    $('node-fields').textContent='';$('node-title').textContent='Choose a node';$('node-detail').textContent='Connect to inspect evidence.';
    $('model-bars').textContent='';$('model-task').textContent='No model result loaded.';$('model-status').textContent='Awaiting data';$('model-limits').textContent='No data loaded.';
    for(const id of ['connect-nodes','reset-layout','add-hypothesis'])$(id).disabled=true;
  });
  $('connect-nodes').addEventListener('click',()=>{linking=!linking;linkSource=null;$('connect-nodes').classList.toggle('active',linking);$('connect-nodes').textContent=linking?'Choose source node':'Connect nodes';});
  $('reset-layout').addEventListener('click',()=>{positions.clear();layout();draw();});
  $('hypothesis-form').addEventListener('submit',event=>{
    event.preventDefault();if(!context||hints.length>=24)return;
    const title=$('hypothesis-label').value.trim();if(!title)return;
    const identity=Array.from(crypto.getRandomValues(new Uint8Array(12)),v=>v.toString(16).padStart(2,'0')).join('');
    hints.push({id:'@hint-'+identity,kind:'hypothesis',title,meta:'Human idea · not evaluated',detail:'User-supplied hypothesis. It has not been tested or applied to any model.',fields:{Source:'human hypothesis',Status:'not evaluated',Control:'none'}});
    $('hypothesis-label').value='';render(context);choose(hints.at(-1).id);
  });
  $('export-map').addEventListener('click',()=>{
    const data={schema:1,purpose:'human_hypotheses_only',applied_to_models:false,requires_review:true,
      nodes:hints.map(n=>({id:n.id,label:n.title})),edges:hintEdges.map(e=>({from:e.from,to:e.to,relationship:e.kind})),note:$('hypothesis-note').value.slice(0,500)};
    const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
    const a=document.createElement('a');a.href=url;a.download='dwellmind-context-hypotheses.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
})();
