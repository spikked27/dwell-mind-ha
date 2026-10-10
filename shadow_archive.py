"""Read-only Influx v1 bootstrap; fixed SELECTs, selected entities, private sources."""
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import hashlib
import ipaddress
import json
import math
import os
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid

from container_app import write_json
from office_collect import rfc3339_ns
from policy import SafeError, identifier, instant
from rooms import ENTITY_ID
from upstream import InfluxClient
from shadow_campaign import STEP, read_document
from shadow_learning import MAX_ROWS

FIELDS=['state','value','state_str','brightness','color_temp','color_temp_kelvin','temperature','current_temperature','current_position','percentage']


class ImportFailure(SafeError):
    def __init__(self,code):
        self.code=code
        super().__init__('Archive import paused; saved source pages are preserved.')


def source_identity(url,database):
    # No credentials or plaintext connection settings in progress documents.
    return hashlib.sha256(json.dumps([url.rstrip('/'),database]).encode()).hexdigest()


def covered_until(cursor,finish,ranges):
    for begin,end in sorted(ranges):
        if begin<=cursor<end:cursor=min(finish,end)
    return cursor


def page_end(source):
    begin=instant(source['start']);end=instant(source['end']);records=source['records']
    if not isinstance(records,list) or len(records)>501 or not begin<end:
        raise ImportFailure('invalid_saved_page')
    previous=None
    for row in records:
        stamp=instant(row['time'])
        if not begin<=stamp<end or previous is not None and stamp<=previous:
            raise ImportFailure('ambiguous_page_order')
        previous=stamp
    return previous+1 if len(records)==501 else end


def failure_code(exc):
    if isinstance(exc,ImportFailure):return exc.code
    known={'Upstream authentication or HTTP request failed.':'upstream_http_error',
           'Upstream authentication failed.':'upstream_authentication_failed',
           'Upstream connection failed.':'upstream_connection_error',
           'Upstream time budget exceeded.':'upstream_timeout',
           'Upstream byte limit exceeded.':'upstream_response_budget',
           'Upstream query rejected.':'upstream_query_error',
           'Failed or partial upstream result.':'upstream_partial_result',
           'Upstream redirect refused.':'upstream_redirect_refused'}
    if isinstance(exc,SafeError):return known.get(str(exc),'source_validation_failed')
    if isinstance(exc,OSError):return 'local_storage_error'
    return 'unexpected_import_error'


def project(entity,metadata,record):
    domain=entity.split('.')[0];state=record.get('state_str',record.get('state'));value=None
    if state is None:state=record.get('state')
    if domain in {'light','binary_sensor','switch','fan','media_player'} and state in {0,1,0.0,1.0}:
        state='on' if state==1 else 'off'
    if domain=='sensor':
        candidate=record.get('value',record.get('state'))
        if candidate is None:candidate=record.get('state')
        low=-10**7 if metadata.get('device_class')=='power' else -100
        if type(candidate) in {int,float} and math.isfinite(candidate) and low<=candidate<=10**7:value=candidate
        state='numeric' if value is not None else 'unknown'
    if not isinstance(state,str):state='unknown'
    attrs={k:v for k,v in record.items() if k in {'brightness','color_temp','color_temp_kelvin','temperature','current_temperature','current_position','percentage'} and type(v) in {int,float} and math.isfinite(v)}
    return {'state':state,'value':value,'attributes':attrs,'unit':metadata.get('unit'),
            'device_class':metadata.get('device_class'),'room':metadata['room'],'availability':'reported' if state not in {'unknown','unavailable','other'} else 'unknown',
            'actor':'unattributed','record_kind':'archive_observation','time':record['time'],'segment':'archive-'+entity,
            'assumed_state':metadata.get('assumed_state',False),'continuity':'archive_carry_limited_to_30_minutes'}


def snapshots(events):
    # Archive gaps/unchanged devices are uncertain. Do not carry a sparse reading
    # indefinitely or interpolate missing sensors, even if this reduces labels.
    state={};result=[];next_time=None;segment=0
    for entity,row in sorted(events,key=lambda v:instant(v[1]['time'])):
        t=instant(row['time'])//1000000
        if next_time is None:next_time=(t//STEP+1)*STEP
        while next_time<t:
            known={e:r for e,r in state.items() if r['availability']=='reported' and next_time-instant(r['time'])//1000000<=1800000}
            if known:result.append({'time':datetime.fromtimestamp(next_time/1000,timezone.utc).isoformat(),'observations':{e:dict(r) for e,r in known.items()}})
            if len(result)>MAX_ROWS:result.pop(0)
            next_time+=STEP
        previous=state.get(entity)
        if previous is None or t-instant(previous['time'])//1000000>1800000 or previous['availability']!='reported':segment+=1
        row['segment']=previous['segment'] if previous and t-instant(previous['time'])//1000000<=1800000 and previous['availability']=='reported' else 'archive-'+str(segment)+'-'+entity
        state[entity]=row
    return result


def replay_sources(directory,allowed,now_ms,move_date,identity=None,reuse_legacy=False):
    """Rebuild bounded old/current training windows from retained private pages."""
    paths=[]
    for path in directory.glob('campaign-*/archive-*/source-*.json'):
        paths.append(path)
        if len(paths)>16384:raise SafeError('Archive replay file budget reached; raw pages retained.')
    old={};recent={};move=instant(move_date)//1000000
    for path in sorted(paths):
        source=read_document(path);entity=source.get('entity_id')
        if entity not in allowed:continue
        if identity is not None and source.get('source_identity')!=identity and not (reuse_legacy and 'source_identity' not in source):continue
        meta=source.get('metadata')
        if not isinstance(meta,dict) or not isinstance(meta.get('room'),str):continue
        records=source.get('records',[])
        if not isinstance(records,list) or len(records)>501:raise SafeError('Invalid archived source page.')
        for record in records:
            t=instant(record['time'])//1000000
            row=(entity,project(entity,meta,record))
            if t<move and len(old.setdefault(entity,[]))<1000:old[entity].append(row)
            elif now_ms-21*86400000<=t<=now_ms:
                bins=recent.setdefault(entity,{})
                slot=t//STEP
                if slot not in bins or instant(bins[slot][1]['time'])<=instant(record['time']):bins[slot]=row
                if len(bins)>2500:del bins[min(bins)]
    historical=snapshots([row for rows in old.values() for row in rows])[:2000]
    current=snapshots([row for bins in recent.values() for row in bins.values()])[-4000:]
    return historical+current


class Archive:
    def __init__(self,campaign,scope):
        self.campaign=campaign;self.scope=scope;self.running=False;self.lock=threading.Lock()
        self.status={'state':'not_configured','queries':0,'source_rows':0,'completed_entities':0,'error':None}
        try:
            paths=list(self.campaign.directory.glob('campaign-*/archive-*/status.json'))
            if len(paths)>1024:raise ImportFailure('progress_directory_budget')
            if paths:
                restored=max((read_document(p) for p in paths),key=lambda s:s['updated_ns'])
                self.status=restored
                if restored['state']=='importing':
                    self.status={**restored,'state':'interrupted','error_code':'worker_interrupted',
                        'error':'Worker stopped during import; re-enter credentials to resume saved ranges.',
                        'credentials_required':True}
        except Exception:
            self.status.update(state='partial',error_code='progress_restore_failed',
                error='Saved progress could not be read; source pages remain untouched.')

    def persist(self,folder):
        self.status['updated_ns']=time.time_ns()
        path=folder/('status-'+uuid.uuid4().hex+'.tmp')
        write_json(path,self.status)
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        try:os.fsync(fd)
        finally:os.close(fd)
        os.replace(path,folder/'status.json')

    def saved_ranges(self,identity,metadata,reuse_legacy):
        ranges=defaultdict(list);pages=0;rows=0;size=0
        for path in self.campaign.directory.glob('campaign-*/archive-*/source-*.json'):
            pages+=1
            if pages>16384:raise ImportFailure('saved_page_budget')
            source=read_document(path);entity=source.get('entity_id')
            matching=source.get('source_identity')==identity
            if not matching and not (reuse_legacy and 'source_identity' not in source):continue
            meta=metadata.get(entity)
            if not meta or any(source.get('metadata',{}).get(k)!=meta.get(k) for k in ('room','unit','device_class','assumed_state')):continue
            measurement=meta.get('unit') if entity.startswith('sensor.') else 'state'
            if source.get('measurement')!=measurement:continue
            end=page_end(source)
            ranges[entity].append((instant(source['start']),end))
            rows+=len(source['records']);size+=path.stat().st_size
        return ranges,rows,size

    def start(self,payload,observations):
        with self.lock:return self._start(payload,observations)

    def _start(self,payload,observations):
        if (not isinstance(payload,dict) or payload.keys()-{'url','database','username','password','start','reuse_legacy'}
                or not {'url','database','username','password','start'}<=payload.keys()
                or type(payload.get('reuse_legacy',False)) is not bool):
            raise SafeError('Explicit local archive connection required.')
        url=urlsplit(payload['url'])
        try:address=ipaddress.ip_address(url.hostname)
        except ValueError:raise SafeError('Use a private LAN IP address for the archive.') from None
        networks=[(0x0A000000,8),(0xAC100000,12),(0xC0A80000,16),'fc00::/7']
        private=any(address in ipaddress.ip_network(n) for n in networks if ipaddress.ip_network(n).version==address.version)
        if not private or url.scheme not in {'http','https'} or url.port!=8086 or url.path not in {'','/'} or url.username or url.password or url.query or url.fragment:
            raise SafeError('Private Influx origin on port 8086 required.')
        identifier(payload['database'])
        if any(not isinstance(payload[k],str) or not 1<=len(payload[k])<=256 or any(ord(c)<32 for c in payload[k]) for k in ['username','password']):
            raise SafeError('Archive credentials required.')
        start=instant(payload['start']);end=time.time_ns()
        if not 0<end-start<=5*366*86400*10**9:raise SafeError('Choose a historical start within five years.')
        allowed=set(self.scope());metadata={e:dict(r) for e,r in observations.items() if e in allowed}
        if self.running or self.campaign.state!='running' or not metadata:raise SafeError('Start a reviewed shadow campaign before importing history.')
        client=InfluxClient(SimpleNamespace(url=payload['url'].rstrip('/'),database=payload['database'],timeout=10),payload['username'],payload['password'])
        identity=source_identity(payload['url'],payload['database'])
        self.running=True;self.status={'state':'importing','queries':0,'source_rows':0,'source_bytes':0,'completed_entities':0,'error':None,'error_code':None,
            'start':payload['start'],'end':rfc3339_ns(end),'read_only_queries':True,'source_identity':identity,
            'reused_source_rows':0,'reused_ranges':0,'credentials_required':False,
            'training_error_code':None,'training_sampling':'Bounded five-minute source bins; raw pages retained.'}
        folder=self.campaign.folder/('archive-'+uuid.uuid4().hex)
        try:
            folder.mkdir(mode=0o700);self.persist(folder)
        except Exception:self.running=False;raise
        # Credentials exist only inside this running import, not saved or returned.
        campaign_id=self.campaign.campaign_id
        def run():
            try:
                ranges,saved_rows,saved_bytes=self.saved_ranges(identity,metadata,payload.get('reuse_legacy',False))
                self.status['reused_source_rows']=saved_rows
                # Recent history first gives usable current-home models promptly.
                recent=max(start,end-21*86400*10**9)
                events=[]
                for phase_begin,phase_end in [(recent,end),(start,recent)]:
                    phase_events=defaultdict(dict)
                    for entity,meta in metadata.items():
                        if not ENTITY_ID.fullmatch(entity):raise SafeError('Invalid selected entity.')
                        measurement=meta.get('unit') if entity.startswith('sensor.') else 'state'
                        if not measurement:continue
                        domain,name=entity.split('.',1);cursor=phase_begin
                        while cursor<phase_end:
                            self.status.update(phase='recent' if phase_begin==recent else 'older',entity_id=entity,next_cursor=rfc3339_ns(cursor))
                            if self.campaign.state!='running' or self.campaign.campaign_id!=campaign_id or entity not in self.scope():raise ImportFailure('scope_or_campaign_changed')
                            advanced=covered_until(cursor,phase_end,ranges[entity])
                            if advanced>cursor:
                                cursor=advanced;self.status['reused_ranges']+=1;continue
                            if self.status['queries']>=4096:raise ImportFailure('query_budget')
                            finish=min(phase_end,cursor+31*86400*10**9)
                            finish=min([finish]+[begin for begin,_ in ranges[entity] if cursor<begin<finish])
                            query='SELECT '+','.join(identifier(f)+'::field' for f in FIELDS)+' FROM '+identifier(measurement)
                            query+=" WHERE \"domain\" = '"+domain+"' AND \"entity_id\" = '"+name+"' AND time >= '"+rfc3339_ns(cursor)+"' AND time < '"+rfc3339_ns(finish)+"' ORDER BY time ASC LIMIT 501"
                            response=client.query(query);self.status['queries']+=1
                            if self.campaign.state!='running' or self.campaign.campaign_id!=campaign_id or entity not in self.scope():raise ImportFailure('scope_or_campaign_changed')
                            series=response.get('series',[])
                            if len(series)>1:raise ImportFailure('ambiguous_series')
                            records=[]
                            for s in series:
                                records=[dict(zip(s['columns'],values)) for values in s.get('values',[])]
                            source_path=folder/('source-'+uuid.uuid4().hex+'.json')
                            source={'entity_id':entity,'measurement':measurement,'source_identity':identity,'metadata':{k:v for k,v in meta.items() if k in {'room','unit','device_class','assumed_state'}},'start':rfc3339_ns(cursor),'end':rfc3339_ns(finish),'records':records,'actor':'unattributed','preference_labels':0}
                            next_cursor=page_end(source)
                            write_json(source_path,source)
                            self.status['source_bytes']+=source_path.stat().st_size
                            if saved_bytes+self.status['source_bytes']>536870912:raise ImportFailure('source_disk_budget')
                            self.status['source_rows']+=len(records)
                            for record in records:
                                record_time=instant(record['time'])
                                selected_window=(phase_begin==recent or record_time<phase_begin+14*86400*10**9 or record_time>=phase_end-14*86400*10**9)
                                if selected_window:
                                    bins=phase_events[entity];slot=record_time//(STEP*1000000)
                                    bins[slot]=(entity,project(entity,meta,record))
                                    if len(bins)>2500:del bins[min(bins)]
                            cursor=next_cursor
                            self.status['next_cursor']=rfc3339_ns(cursor);self.persist(folder)
                            time.sleep(1) # Fixed I/O pacing; no unbounded database fan-out.
                        self.status['completed_entities']+=1
                    try:
                        part=snapshots([row for bins in phase_events.values() for row in bins.values()])
                        with self.campaign.lock:
                            existing=list(self.campaign.samples)
                            # Keep paired old-home windows for transfer evaluation, plus
                            # a larger current-home sample; all raw source pages survive.
                            if phase_begin==recent:events=part
                            else:
                                old=part[:2000];events=old+events[-4000:]
                            combined={r['time']:r for r in events+existing}
                            ordered=[combined[t] for t in sorted(combined)]
                            if len(ordered)>MAX_ROWS:ordered=ordered[:2000]+ordered[-4000:]
                            self.campaign.samples.clear();self.campaign.samples.extend(ordered)
                            self.campaign.last_training=0;self.campaign.retrain(set(self.scope()))
                    except Exception:self.status['training_error_code']='phase_training_unavailable'
                # Reused pages also feed training; failure here must not undo a
                # completed download or turn a sampling cap into an import failure.
                try:
                    restored=replay_sources(self.campaign.directory,set(self.scope()),end//1000000,self.campaign.policy['move_date'],identity,payload.get('reuse_legacy',False))
                    with self.campaign.lock:
                        combined={r['time']:r for r in restored+list(self.campaign.samples)}
                        ordered=[combined[t] for t in sorted(combined)]
                        if len(ordered)>MAX_ROWS:ordered=ordered[:2000]+ordered[-4000:]
                        self.campaign.samples.clear();self.campaign.samples.extend(ordered)
                        self.campaign.last_training=0;self.campaign.retrain(set(self.scope()))
                except Exception:self.status['training_error_code']='source_replay_unavailable'
                self.status['state']='completed'
            except Exception as exc:
                self.status.update(state='partial',error_code=failure_code(exc),
                    error='Archive import paused; saved pages retained. Re-enter credentials to resume unfinished ranges.',credentials_required=True)
            finally:
                try:self.persist(folder)
                except Exception:self.status.update(state='partial',error_code='progress_storage_error',error='Progress could not be saved; existing source pages retained.')
                self.running=False
        try:threading.Thread(target=run,name='dwellmind-archive-import',daemon=True).start()
        except Exception:
            self.running=False;self.status.update(state='partial',error_code='worker_thread_unavailable')
            self.persist(folder);raise
        return dict(self.status)
