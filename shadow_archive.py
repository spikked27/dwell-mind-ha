"""Read-only Influx v1 bootstrap; fixed SELECTs, selected entities, private sources."""
from datetime import datetime, timedelta, timezone
import ipaddress
import math
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
from shadow_campaign import STEP
from shadow_learning import MAX_ROWS

FIELDS=['state','value','state_str','brightness','color_temp','color_temp_kelvin','temperature','current_temperature','current_position','percentage']


def project(entity,metadata,record):
    domain=entity.split('.')[0];state=record.get('state_str',record.get('state'));value=None
    if state is None:state=record.get('state')
    if domain in {'light','binary_sensor','switch','fan','media_player'} and state in {0,1,0.0,1.0}:
        state='on' if state==1 else 'off'
    if domain=='sensor':
        candidate=record.get('value',record.get('state'))
        if candidate is None:candidate=record.get('state')
        if type(candidate) in {int,float} and math.isfinite(candidate) and -100<=candidate<=10**7:value=candidate
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


class Archive:
    def __init__(self,campaign,scope):
        self.campaign=campaign;self.scope=scope;self.running=False
        self.status={'state':'not_configured','queries':0,'source_rows':0,'completed_entities':0,'error':None}

    def start(self,payload,observations):
        if not isinstance(payload,dict) or payload.keys()!={'url','database','username','password','start'}:
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
        self.running=True;self.status={'state':'importing','queries':0,'source_rows':0,'source_bytes':0,'completed_entities':0,'error':None,'start':payload['start'],'read_only_queries':True}
        folder=self.campaign.folder/('archive-'+uuid.uuid4().hex);folder.mkdir(mode=0o700)
        # Credentials exist only inside this running import, not saved or returned.
        client=InfluxClient(SimpleNamespace(url=payload['url'].rstrip('/'),database=payload['database'],timeout=10),payload['username'],payload['password'])
        campaign_id=self.campaign.campaign_id
        def run():
            try:
                # Recent history first gives usable current-home models promptly.
                recent=max(start,end-21*86400*10**9)
                events=[]
                for phase_begin,phase_end in [(recent,end),(start,recent)]:
                    phase_events=[]
                    for entity,meta in metadata.items():
                        if not ENTITY_ID.fullmatch(entity):raise SafeError('Invalid selected entity.')
                        measurement=meta.get('unit') if entity.startswith('sensor.') else 'state'
                        if not measurement:continue
                        domain,name=entity.split('.',1);cursor=phase_begin
                        while cursor<phase_end:
                            if self.campaign.state!='running' or self.campaign.campaign_id!=campaign_id or entity not in self.scope():raise SafeError('Scope or campaign changed.')
                            if self.status['queries']>=4096:raise SafeError('Archive query/source budget reached; partial sources retained.')
                            finish=min(phase_end,cursor+31*86400*10**9)
                            query='SELECT '+','.join(identifier(f)+'::field' for f in FIELDS)+' FROM '+identifier(measurement)
                            query+=" WHERE \"domain\" = '"+domain+"' AND \"entity_id\" = '"+name+"' AND time >= '"+rfc3339_ns(cursor)+"' AND time < '"+rfc3339_ns(finish)+"' ORDER BY time ASC LIMIT 501"
                            response=client.query(query);self.status['queries']+=1
                            if self.campaign.state!='running' or self.campaign.campaign_id!=campaign_id or entity not in self.scope():raise SafeError('Scope or campaign changed.')
                            series=response.get('series',[])
                            if len(series)>1:raise SafeError('Ambiguous historical series; reconcile mappings first.')
                            records=[]
                            for s in series:
                                records=[dict(zip(s['columns'],values)) for values in s.get('values',[])]
                            source_path=folder/('source-'+uuid.uuid4().hex+'.json')
                            write_json(source_path,{'entity_id':entity,'measurement':measurement,'start':rfc3339_ns(cursor),'end':rfc3339_ns(finish),'records':records,'actor':'unattributed','preference_labels':0})
                            self.status['source_bytes']+=source_path.stat().st_size
                            if self.status['source_bytes']>536870912:raise SafeError('Archive disk budget reached; sources retained.')
                            self.status['source_rows']+=len(records)
                            for record in records:
                                record_time=instant(record['time'])
                                selected_window=(phase_begin==recent or record_time<phase_begin+14*86400*10**9 or record_time>=phase_end-14*86400*10**9)
                                if selected_window:
                                    if len(phase_events)>=100000:raise SafeError('Phase event memory budget reached; sources retained.')
                                    phase_events.append((entity,project(entity,meta,record)))
                            if len(records)==501:
                                stamp=instant(records[-1]['time'])
                                if stamp<cursor:raise SafeError('Archive pagination refused.')
                                cursor=stamp+1
                            else:cursor=finish
                            time.sleep(1) # Fixed I/O pacing; no unbounded database fan-out.
                        self.status['completed_entities']+=1
                    part=snapshots(phase_events)
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
                self.status['state']='completed'
            except Exception:
                self.status['state']='partial';self.status['error']='Archive import stopped; available sources preserved. Check scope, mappings, connection and budgets.'
            finally:self.running=False
        threading.Thread(target=run,name='dwellmind-archive-import',daemon=True).start()
        return dict(self.status)
