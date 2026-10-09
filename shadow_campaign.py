"""Shadow campaign: bounded training, inert decisions and later observed outcomes."""
from collections import deque
from datetime import datetime, timezone
import os
import math
from pathlib import Path
import stat
import threading
import uuid
from zoneinfo import ZoneInfo

from container_app import write_json
from policy import SafeError, instant, strict_json
from private_journal import Journal
from shadow_learning import train, features, infer, channels, propose, time_ms, MAX_ROWS
from preference_learning import train_preferences

STEP=300000


def read_document(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as handle:
        info=os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)&0o077:
            raise SafeError('Private campaign document required.')
        raw=handle.read(2097153)
        if len(raw)>2097152:raise SafeError('Campaign document budget exceeded.')
        return strict_json(raw)


def read_rows(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as handle:
        info=os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)&0o077:
            raise SafeError('Campaign source must be a private regular file.')
        budget=0
        while raw:=handle.readline(16385):
            budget+=len(raw)
            if len(raw)>16384 or budget>16777216:raise SafeError('Campaign source exceeds budget.')
            yield strict_json(raw)


def history_snapshots(directory, allowed):
    """Replay selected immutable capture journals; gaps break individual continuity."""
    result=deque(maxlen=MAX_ROWS);state={};next_time=None;previous=None;sequence=0
    folders=sorted((Path(directory)/'captures').glob('capture-*'),key=lambda p:p.stat().st_mtime_ns)
    if len(folders)>64:raise SafeError('Capture source directory budget exceeded.')
    for folder in folders:
        state={};next_time=None;previous=None;sequence+=1
        paths=sorted(folder.glob('events-*.jsonl'))
        if len(paths)>4:raise SafeError('Journal source budget exceeded.')
        for path in paths:
            for row in read_rows(path):
                t=time_ms(row['time'])
                if previous is not None and t<previous:raise SafeError('Source history is out of order.')
                previous=t
                if next_time is None:next_time=(t//STEP+1)*STEP
                while next_time<t:
                    if state:
                        result.append({'time':datetime.fromtimestamp(next_time/1000,timezone.utc).isoformat(),
                                       'observations':{e:dict(r) for e,r in state.items()}})
                    next_time+=STEP
                entity=row.get('entity_id')
                if entity not in allowed:continue
                if row.get('availability')!='reported':
                    state.pop(entity,None);sequence+=1
                else:
                    old=state.get(entity)
                    state[entity]={k:v for k,v in row.items() if k in {'state','value','unit','device_class','attributes','availability','room','actor','record_kind','time','assumed_state'}}
                    state[entity]['segment']=old['segment'] if old else str(sequence)+'-'+entity
    # Overlapping/restarted captures can duplicate wall-clock bins; keep the last
    # evidence for a time without ever stitching their continuity identifiers.
    by_time={row['time']:row for row in result}
    return [by_time[t] for t in sorted(by_time)]


class Campaign:
    def __init__(self,directory,wall):
        self.directory=Path(directory)/'shadow';self.wall=wall
        self.state='not_started';self.error=None;self.models={};self.reports={};self.policy=None
        self.samples=deque(maxlen=MAX_ROWS);self.decisions=deque(maxlen=40);self.pending=[]
        self.evaluated=0;self.correct=0;self.unknown=0;self.issued=0;self.last_sample=None;self.last_training=0
        self.training=False;self.journal=None;self.lock=threading.RLock();self.segment=0;self.entity_segments={}
        self.holds={}
        self.reviewed={};self.preference_examples={};self.preference_models={};self.preference_reports={};self.decision_inputs={}
        self.acceptance_examples={};self.acceptance_models={}
        self.review_records={}
        try:
            folders=sorted(self.directory.glob('campaign-*'),key=lambda p:p.stat().st_mtime_ns)
            if len(folders)>32:raise SafeError('Campaign directory cap reached.')
            if folders:
                folder=folders[-1];self.policy=read_document(folder/'policy.json')
                if not (folder/'stopped.json').exists() and self.policy.get('deadline_ms',0)>self.wall()//1000000:
                    self.state='recovery_pending'
                    model_paths=sorted(folder.glob('model-*.json'),key=lambda p:p.stat().st_mtime_ns)
                    if model_paths:
                        result=read_document(model_paths[-1]);self.models=result['models'];self.reports=result['reports'];self.preference_models=result.get('preferences',{});self.acceptance_models=result.get('acceptance',{})
                    feedback_paths=list(folder.glob('feedback-*.json'))
                    if len(feedback_paths)>1024:raise SafeError('Review retention cap reached.')
                    latest_reviews={}
                    for path in feedback_paths:
                        review=read_document(path)
                        if review.get('source')!='explicit_human_review':raise SafeError('Invalid review source.')
                        previous=latest_reviews.get(review['decision_id'])
                        if previous is None or review.get('revision',1)>previous.get('revision',1):latest_reviews[review['decision_id']]=review
                    for review in latest_reviews.values():
                        self.review_records[review['decision_id']]=review
                        self.reviewed[review['decision_id']]=review['verdict']
                        key=review['target']+'|'+review['channel']
                        if review['verdict']=='appropriate':self.preference_examples.setdefault(key,[]).append(review)
                        if review['verdict'] in {'appropriate','inappropriate'}:
                            accepted={**review,'x':{**review['x'],'@candidate_prediction':review['y']},'y':review['verdict'],'current':'inappropriate'}
                            self.acceptance_examples.setdefault(key,[]).append(accepted)
                    if model_paths and result.get('feedback_revisions',{})!={k:v.get('revision',1) for k,v in latest_reviews.items()}:
                        self.preference_models={};self.acceptance_models={}
        except Exception:self.error='Campaign recovery unavailable; history retained.'

    def resume(self,allowed):
        if self.state!='recovery_pending' or not allowed:return
        deadline=self.policy['deadline_ms'];remaining=deadline-self.wall()//1000000
        if remaining<=0:self.state='completed';return
        payload={'days':min(30,max(1,math.ceil(remaining/86400000))),'move_date':self.policy['move_date'],'timezone':self.policy['timezone']}
        self.start(payload,allowed,deadline)

    def on_row(self,row):
        if row.get('record_kind')=='state_update' and row.get('actor') in {'user_associated','unattributed'}:
            self.holds[row['entity_id']]=(time_ms(row['time']),row['actor'])

    def feedback(self,payload,allowed):
        if not isinstance(payload,dict) or payload.keys()!={'decision_id','verdict'} or payload['verdict'] not in {'appropriate','inappropriate','uncertain'}:
            raise SafeError('Explicit appropriate/inappropriate/uncertain review required.')
        identity=payload['decision_id']
        with self.lock:
            decision=next((d for d in self.decisions if d['decision_id']==identity),None)
            inputs=self.decision_inputs.get(identity)
            if decision and decision['target'].startswith('binary_sensor.'):
                raise SafeError('Sensor evidence cannot become a desired device preference.')
            if not decision or not inputs or decision['target'] not in allowed or any(k.split('|')[0] not in allowed for k in inputs if not k.startswith('@')):
                raise SafeError('Review requires a recent prediction entirely within current scope.')
            if self.reviewed.get(identity)==payload['verdict']:return self.view(allowed)
            if len(list(self.folder.glob('feedback-*.json')))>=1024:raise SafeError('Review retention cap reached; earlier labels retained.')
            review={'source':'explicit_human_review','decision_id':identity,'time':decision['issued_ms'],
                    'target':decision['target'],'channel':decision['channel'],'verdict':payload['verdict'],
                    'x':inputs,'y':decision['predicted'],'current':decision['current'],'executed':False,
                    'unit':decision.get('unit'),
                    'revision':self.review_records.get(identity,{}).get('revision',0)+1}
            write_json(self.folder/('feedback-'+uuid.uuid4().hex+'.json'),review)
            self.reviewed[identity]=payload['verdict'];decision['human_review']=payload['verdict']
            self.review_records[identity]=review
            key=decision['target']+'|'+decision['channel']
            self.preference_models.pop(key,None);self.acceptance_models.pop(key,None);self.preference_reports.pop(key,None);self.last_training=0
            for collection in [self.preference_examples,self.acceptance_examples]:
                if key in collection:collection[key]=[e for e in collection[key] if e['decision_id']!=identity]
            # A rejection is not an opposite preference. Uncertain votes are not labels.
            if payload['verdict']=='appropriate':
                self.preference_examples.setdefault(key,[]).append(review)
                self.last_training=0
            if payload['verdict'] in {'appropriate','inappropriate'}:
                accepted={**review,'x':{**inputs,'@candidate_prediction':decision['predicted']},'y':payload['verdict'],'current':'inappropriate'}
                self.acceptance_examples.setdefault(key,[]).append(accepted);self.last_training=0
            return self.view(allowed)

    def start(self,payload,allowed,deadline=None):
        if not isinstance(payload,dict) or payload.keys()!={'days','move_date','timezone'} or type(payload['days']) is not int or not 1<=payload['days']<=30:
            raise SafeError('Explicit one to thirty day shadow campaign required.')
        if not allowed:raise SafeError('Select rooms and entities first.')
        zone=ZoneInfo(payload['timezone'])
        if isinstance(payload['move_date'],str) and len(payload['move_date'])==10:
            payload={**payload,'move_date':datetime.fromisoformat(payload['move_date']).replace(tzinfo=zone).isoformat()}
        if time_ms(payload['move_date'])>=self.wall()//1000000:raise SafeError('Move boundary must be in the past.')
        with self.lock:
            if self.state=='running':raise SafeError('Stop the active shadow campaign first.')
            self.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
            if len(list(self.directory.glob('campaign-*')))>=32:raise SafeError('Campaign archive cap reached; no history deleted.')
            self.campaign_id='campaign-'+uuid.uuid4().hex;self.folder=self.directory/self.campaign_id
            self.folder.mkdir(mode=0o700)
            self.journal=Journal(self.folder,'decisions',max_file_bytes=16777216)
            self.deadline=deadline or self.wall()//1000000+payload['days']*86400000
            self.policy={'manual_hold_seconds':1800,'night_brightness_max':51,'climate_min':18,'climate_max':24,
                         'night_start':22,'night_end':7,'timezone':payload['timezone'],'move_date':payload['move_date'],
                         'duration_days':payload['days'],'control_enabled':False,'deadline_ms':self.deadline}
            write_json(self.folder/'policy.json',self.policy)
            self.state='running';self.error=None;self.last_sample=None
            self.training=True;identity=self.campaign_id
            def bootstrap():
                try:
                    rows=history_snapshots(self.directory.parent,allowed)
                    from shadow_archive import replay_sources
                    try:rows=replay_sources(self.directory,allowed,self.wall()//1000000,self.policy['move_date'])+rows
                    except Exception:self.error='Archive replay partial/unavailable; raw sources and prior models retained.'
                    with self.lock:
                        if identity!=self.campaign_id:return
                        current=list(self.samples);merged={r['time']:r for r in rows+current}
                        ordered=[merged[t] for t in sorted(merged)]
                        if len(ordered)>MAX_ROWS:ordered=ordered[:2000]+ordered[-4000:]
                        self.samples=deque(ordered,maxlen=MAX_ROWS)
                        self.training=False;self.retrain(allowed)
                except Exception:
                    with self.lock:self.training=False;self.error='Retained capture replay unavailable; new observations still collected.'
            threading.Thread(target=bootstrap,name='dwellmind-capture-bootstrap',daemon=True).start()
        return self.view(allowed)

    def stop(self):
        with self.lock:
            self.gap()
            if self.journal:self.journal.close();self.journal=None
            self.state='stopped';self.pending=[]
            if hasattr(self,'folder') and not (self.folder/'stopped.json').exists():
                write_json(self.folder/'stopped.json',{'state':'stopped','control_enabled':False})

    def gap(self):
        with self.lock:
            self.segment+=1;self.entity_segments={}
            for pending in self.pending:
                pending['outcome']=None;pending['outcome_status']='unknown coverage'
                if self.journal:
                    try:self.journal.write({'kind':'outcome','decision_id':pending['decision_id'],'time_ms':self.wall()//1000000,'outcome':None,'coverage':'unknown','executed':False})
                    except Exception:
                        self.error='Shadow outcome journal unavailable; passive capture continues.'
                        self.journal.close();self.journal=None;self.state='storage_error'
            self.unknown+=len(self.pending);self.pending=[]

    def expire(self):
        if self.state=='running' and self.wall()//1000000>=self.deadline:
            self.stop();self.state='completed'

    def retrain(self,allowed):
        if self.training or len(self.samples)<300:return
        self.training=True;rows=list(self.samples);policy=dict(self.policy);scope=set(allowed)
        self.last_training=self.wall()//1000000
        folder=self.folder;campaign_id=self.campaign_id
        reviews={k:[dict(e) for e in values if all(name.startswith('@') or name.split('|')[0] in scope for name in e['x'])] for k,values in self.preference_examples.items() if k.split('|')[0] in scope}
        acceptance_reviews={k:[dict(e) for e in values if all(name.startswith('@') or name.split('|')[0] in scope for name in e['x'])] for k,values in self.acceptance_examples.items() if k.split('|')[0] in scope}
        review_versions={k:v.get('revision',1) for k,v in self.review_records.items()}
        rows=[{'time':r['time'],'observations':{e:v for e,v in r['observations'].items() if e in scope}} for r in rows]
        def run():
            try:
                result=train(rows,policy['move_date'],scope)
                preferences={}
                for key,examples in reviews.items():
                    if len(examples)<100:continue
                    try:preferences[key]=train_preferences(examples)
                    except SafeError:continue
                acceptance={}
                for key,examples in acceptance_reviews.items():
                    if len(examples)<100:continue
                    try:acceptance[key]=train_preferences(examples)
                    except SafeError:continue
                with self.lock:
                    if self.campaign_id!=campaign_id or self.state!='running':return
                    # Scope lineage is rechecked at inference; no model can revive exclusions.
                    identity='model-'+uuid.uuid4().hex
                    if len(list(self.folder.glob('model-*.json')))>=128:raise SafeError('Model retention cap reached; source history preserved.')
                    result['model_id']=identity;result['trained_at']=datetime.now(timezone.utc).isoformat()
                    result['preferences']=preferences;result['acceptance']=acceptance
                    result['feedback_revisions']=review_versions
                    if review_versions!={k:v.get('revision',1) for k,v in self.review_records.items()}:
                        result['preferences']={};result['acceptance']={};preferences={};acceptance={}
                    write_json(folder/(identity+'.json'),result)
                    self.models=result['models'];self.reports=result['reports']
                    self.preference_models=preferences
                    self.acceptance_models=acceptance
                    self.preference_reports={k:{name:value for name,value in v.items() if name!='model'} for k,v in preferences.items()}
                    self.last_training=self.wall()//1000000
            except Exception:
                with self.lock:self.error='Training unavailable; prior models and data retained.'
            finally:
                with self.lock:self.training=False
        threading.Thread(target=run,name='dwellmind-shadow-training',daemon=True).start()

    def sample(self,observations,allowed):
        with self.lock:
            if self.state!='running':return
            now=self.wall()//1000000
            if now>=self.deadline:self.stop();self.state='completed';return
            slot=now//STEP*STEP
            if slot==self.last_sample:return
            permitted={e:dict(row) for e,row in observations.items() if e in allowed}
            for e,row in permitted.items():
                if row.get('availability')!='reported':self.entity_segments.pop(e,None)
                else:self.entity_segments.setdefault(e,str(self.segment)+'-'+e)
                row['segment']=self.entity_segments.get(e)
            for pending in self.pending:
                row=permitted.get(pending['target'],{})
                current=channels(pending['target'],row).get(pending['channel'])
                valid=(current is not None and row.get('segment')==pending['segment'] and 240000<=now-pending['issued_ms']<=360000)
                if valid:
                    self.evaluated+=1;self.correct+=int(current==pending['predicted'])
                else:self.unknown+=1
                pending['outcome']=current if valid else None
                pending['outcome_status']='matched reported outcome' if valid and current==pending['predicted'] else 'different reported outcome' if valid else 'unknown coverage'
                self.journal.write({'kind':'outcome','decision_id':pending['decision_id'],'time_ms':slot,
                                    'outcome':current if valid else None,'coverage':'reported' if valid else 'unknown','executed':False})
            self.pending=[]
            timestamp=datetime.fromtimestamp(now/1000,timezone.utc).isoformat()
            past=self.samples[-1] if self.samples and 240000<=now-time_ms(self.samples[-1]['time'])<=360000 else None
            self.samples.append({'time':timestamp,'observations':permitted})
            self.last_sample=slot
            hour=datetime.fromtimestamp(now/1000,ZoneInfo(self.policy['timezone'])).hour
            for identity,model in self.models.items():
                entity=model['entity_id'];channel=model['channel'];row=permitted.get(entity,{})
                if entity not in allowed or any(e not in allowed for e in model['lineage']):continue
                current=channels(entity,row).get(channel)
                if current is None:continue
                result=infer(model['model'],features(permitted,model['room'],now,past['observations'] if past else None))
                inputs=features(permitted,model['room'],now,past['observations'] if past else None)
                policy={**self.policy,'night':hour>=self.policy['night_start'] or hour<self.policy['night_end']}
                # Temperature bounds are unit-specific; unknown units prohibit proposals.
                if row.get('unit')=='°F':policy.update(climate_min=65,climate_max=75)
                decision=propose(identity,model,result,current,permitted,policy,now)
                if identity in self.preference_models and all(name.startswith('@') or name.split('|')[0] in allowed for name in self.preference_models[identity]['model']['features']):
                    preference=self.preference_models[identity];desired=infer(preference['model'],inputs)
                    decision['preference_forecast']={'state':preference['state'],'predicted':desired['prediction'],
                        'probability':max(desired['probabilities'].values()),'abstained':desired['abstained']}
                    if preference['state']=='validated_preference_candidate' and not desired['abstained'] and max(desired['probabilities'].values())>=.75:
                        decision['recommended_preference']=desired['prediction']
                        decision['desired_action_learned']=True
                        checked=propose(identity,{**model,'evaluation_state':'beats_baselines'},desired,current,permitted,policy,now)
                        decision['preference_candidate_boundaries']=[r for r in checked['blocked_by'] if r!='preference_not_established']
                        if desired['prediction']==decision['predicted']:
                            decision['blocked_by'].remove('preference_not_established')
                        else:decision['blocked_by'].append('behavior_forecast_differs_from_reviewed_preference')
                if identity in self.acceptance_models and all(name.startswith('@') or name.split('|')[0] in allowed for name in self.acceptance_models[identity]['model']['features']):
                    acceptable=infer(self.acceptance_models[identity]['model'],{**inputs,'@candidate_prediction':decision['predicted']})
                    decision['reviewed_appropriateness_probability']=acceptable['probabilities'].get('appropriate',0)
                    if acceptable['abstained'] or acceptable['probabilities'].get('appropriate',0)<.75:
                        decision['blocked_by'].append('learned_from_reviews_as_unwanted_or_uncertain')
                hold=self.holds.get(entity)
                if hold and now-hold[0]<policy['manual_hold_seconds']*1000:
                    decision['blocked_by'].append('manual_or_unattributed_change_hold')
                decision.update(decision_id=uuid.uuid4().hex,issued_ms=now,target_ms=now+STEP,segment=row.get('segment'),
                                model_state=model['evaluation_state'],room=model['room'],unit=row.get('unit'))
                self.journal.write({**decision,'candidate_kind':decision['kind'],'kind':'prediction'});self.decisions.appendleft(decision)
                self.decision_inputs[decision['decision_id']]=inputs
                keep={d['decision_id'] for d in self.decisions}
                self.decision_inputs={k:v for k,v in self.decision_inputs.items() if k in keep}
                self.pending.append(decision);self.issued+=1
            if now-self.last_training>=6*3600000:self.retrain(allowed)

    def view(self,allowed):
        with self.lock:
            reports={}
            for k,v in list(self.reports.items())[:32]:
                if k.split('|')[0] not in allowed:continue
                reports[k]=dict(v)
                if k in self.models and any(e not in allowed for e in self.models[k]['lineage']):
                    reports[k]['state']='excluded_lineage';reports[k]['historical_result_preserved']=True
            return {'state':self.state,'training':self.training,'error':self.error,'snapshot_examples':len(self.samples),
                    'model_targets':len([m for m in self.models.values() if m['entity_id'] in allowed and all(e in allowed for e in m['lineage'])]),
                    'predictions_issued':self.issued,'evaluated':self.evaluated,'matched_reported_outcomes':self.correct,
                    'unknown_outcomes':self.unknown,'execution_enabled':False,
                    'preference_labels':sum(len(v) for v in self.preference_examples.values()),'human_reviews':len(self.reviewed),
                    'preference_reports':{k:v for k,v in self.preference_reports.items() if k.split('|')[0] in allowed},
                    'policy':self.policy,'deadline_ms':getattr(self,'deadline',None),
                    'reports':reports,
                    'decisions':[d for d in self.decisions if d['target'] in allowed and all(e['feature'].startswith('@') or e['feature'].split('|')[0] in allowed for e in d['evidence'])][:20]}
