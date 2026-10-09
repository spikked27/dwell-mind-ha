"""Authenticated local observation worker. No HA credentials or control API."""
import hashlib
import hmac
import ipaddress
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import threading
import time
import uuid
from urllib.parse import urlsplit

from container_app import VERSION, private_directory, write_json
from capture_summary import ReportReader
from live_report import load_journals, summarize
from office_collect import rfc3339_ns
from passive_observer import Observer
from policy import SafeError, instant, strict_json
from private_journal import Journal
from rooms import ENTITY_ID
from upstream import read_secret
from thermal_forecast import train, forecast_window
from shadow_campaign import Campaign
from shadow_archive import Archive

MAX_BODY = 131072
MAX_TRAIN_BODY = 4194304
MAX_ENTITIES = 40
UI_DIRECTORY = Path(__file__).resolve().parent/'web'
UI_ASSETS = {'/ui':('index.html','text/html; charset=utf-8'),
             '/ui/':('index.html','text/html; charset=utf-8'),
             '/ui/studio.css':('studio.css','text/css; charset=utf-8'),
             '/ui/studio.js':('studio.js','text/javascript; charset=utf-8'),
             '/ui/icon.svg':('icon.svg','image/svg+xml')}
PRIVATE_NETWORKS = [ipaddress.ip_network(value) for value in
                    [(0x0A000000,8),(0xAC100000,12),(0xC0A80000,16),'127.0.0.0/8','::1/128','fc00::/7']]


def private_peer(value):
    try:
        address = ipaddress.ip_address(value)
        if address.version == 6 and address.ipv4_mapped:
            address = address.ipv4_mapped
        return any(address in network for network in PRIVATE_NETWORKS)
    except ValueError:
        return False


class Selection:
    def __init__(self, value):
        if not isinstance(value, dict) or value.keys() != {'area_id', 'name', 'entities'}:
            raise SafeError('Invalid room selection.')
        if not isinstance(value['area_id'], str) or not 1 <= len(value['area_id']) <= 64:
            raise SafeError('Invalid area identifier.')
        self.area_id = value['area_id']
        self.name = value['name']
        if not isinstance(self.name, str) or not 1 <= len(self.name) <= 64 or any(ord(c) < 32 for c in self.name):
            raise SafeError('Invalid room name.')
        entities = value['entities']
        if not isinstance(entities, list) or len(entities) > MAX_ENTITIES:
            raise SafeError('Invalid room entities.')
        for entity in entities:
            if not isinstance(entity, str) or not ENTITY_ID.fullmatch(entity) or entity.split('.')[0] not in {'light','binary_sensor','sensor','media_player','climate','cover','fan','valve','switch'}:
                raise SafeError('Unsupported observation entity.')
        if len(set(entities)) != len(entities):
            raise SafeError('Duplicate room entities.')
        self.entities = dict.fromkeys(entities)


def selections(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise SafeError('Select one to eight rooms.')
    profiles = [Selection(v) for v in value]
    entities = [e for p in profiles for e in p.entities]
    if not 1 <= len(entities) <= MAX_ENTITIES or len(set(entities)) != len(entities):
        raise SafeError('Select at most 40 distinct entities.')
    if len({p.area_id for p in profiles}) != len(profiles) or len({p.name for p in profiles}) != len(profiles):
        raise SafeError('Duplicate room selection.')
    return profiles


def service_secret(directory):
    path = Path(directory)/'service-token'
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(secrets.token_urlsafe(48))
    token = read_secret(str(path))
    if not 32 <= len(token) <= 128 or not token.isascii() or not token.isprintable():
        raise SafeError('Invalid worker pairing key.')
    return token


class Worker:
    def __init__(self, directory, clock=time.monotonic, wall=time.time_ns):
        self.directory = Path(directory)
        for path in (self.directory, self.directory/'captures', self.directory/'reports', self.directory/'learning'):
            private_directory(path)
        self.clock, self.wall = clock, wall
        self.lock = threading.RLock()
        self.profiles, self.selection = [], []
        self.journal = None
        self.run_id = None
        self.state = 'idle'
        self.last_report = None
        self.report_reader = ReportReader(self.directory/'reports')
        self.report_reader_error = False
        try:
            restored = self.report_reader.latest()
            if restored:
                self.last_report = restored['report_id']
        except (SafeError,OSError,ValueError,TypeError):
            self.report_reader_error = True
        self.gaps = 0
        self.rows = 0
        self.last_stamp = 0
        self.last_sequence = 0
        self.last_digest = None
        self.last_contact = 0
        self.await_snapshot = True
        self.initialized = set()
        self.learning_summary = None
        self.learning_model = None
        self.forecast = None
        self.learning_error = False
        self.live_observations = {}
        self.shadow = Campaign(self.directory,self.wall)
        self.archive = Archive(self.shadow,lambda:{e for p in self.profiles for e in p.entities})
        try:
            paths = sorted((self.directory/'learning').glob('learning-*.json'))
            if len(paths) > 16:
                raise SafeError('Learning report retention cap reached.')
            latest = None
            for path in paths:
                if not re.fullmatch(r'learning-[0-9a-f]{32}\.json', path.name):
                    continue
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, 'rb') as handle:
                    info = os.fstat(handle.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode)&0o077:
                        raise SafeError('Learning result must be a private regular file.')
                    raw = handle.read(131073)
                if len(raw) > 131072:
                    raise SafeError('Learning result exceeds byte budget.')
                result = strict_json(raw)
                if result.get('control_enabled') is not False or result.get('task') != 'next_hour_mean_temperature_forecast':
                    raise SafeError('Invalid learning result.')
                if latest is None or instant(result['trained_at']) > instant(latest['trained_at']):
                    latest = result
            if latest:
                self.learning_model = latest
                self.learning_summary = self.project_learning(latest)
        except (SafeError,OSError,ValueError,TypeError,KeyError):
            self.learning_error = True

    def stamp(self):
        self.last_stamp = max(self.wall(), self.last_stamp + 1)
        return rfc3339_ns(self.last_stamp)

    def status(self):
        with self.lock:
            self.tick()
            shadow=self.shadow.view({e for p in self.profiles for e in p.entities})
            summary={k:v for k,v in shadow.items() if k not in {'decisions','reports','preference_reports'}}
            summary['target_reports']={name:{'state':report['state'],'examples':report.get('examples'),
                'current_home_examples':report.get('current_home_examples'),'old_home_examples':report.get('old_home_examples'),
                'selected_on_validation':report.get('selected_on_validation'),
                'held_out':{baseline:{metric:value for metric,value in metrics.items() if metric in {'brier','balanced_accuracy','examples'}} for baseline,metrics in report.get('held_out',{}).items()}}
                for name,report in shadow['reports'].items()}
            return {'product':'DwellMind HA', 'version':VERSION, 'protocol':1,
                    'state':self.state, 'capture_id':self.run_id,
                    'room_count':len(self.profiles), 'entity_count':sum(len(p.entities) for p in self.profiles),
                    'rows':self.rows, 'gaps':self.gaps, 'initialized_entities':len(self.initialized),
                    'last_report':self.last_report, 'control_enabled':False,
                    'capabilities':['latest_capture_summary','temperature_forecast_training','explicit_exclusions','temperature_live_forecast','shadow_campaign','periodic_scope_refresh'],
                    'report_reader_error':self.report_reader_error, 'learning_error':self.learning_error,
                    'learning_summary':self.learning_summary,
                    'shadow_summary':summary}

    @staticmethod
    def project_learning(result):
        keys = {'task','entity_id','unit','coverage','source_start','source_end','validation_start','test_start',
                'selected_on_validation','validation_candidates','validation_baselines','held_out_test',
                'mae_improvement_over_best_baseline_percent','evaluation_status','limitations','trained_at','model_id'}
        return {k:v for k,v in result.items() if k in keys}

    def train_temperature(self, payload):
        if (not isinstance(payload,dict) or payload.keys() != {'entity_id','unit','move_date','start','end','rows'}
                or not isinstance(payload['entity_id'],str) or not payload['entity_id'].startswith('sensor.')
                or payload['entity_id'] not in {e for p in self.profiles for e in p.entities}):
            raise SafeError('Training requires one selected temperature entity.')
        # An explicit training action preserves the source and model in a fresh,
        # private directory; no overwrites, history deletion or automatic promotion.
        with self.lock:
            folder = self.directory/'learning'
            if len(list(folder.glob('learning-*.json'))) >= 16:
                raise SafeError('Learning retention cap reached; archive locally before training again.')
            result = train(payload)
            identity = 'learning-'+uuid.uuid4().hex
            result.update(model_id=identity, trained_at=self.stamp())
            write_json(folder/('history-'+identity[9:]+'.json'), payload)
            write_json(folder/(identity+'.json'), result)
            self.learning_summary = self.project_learning(result)
            self.learning_model = result
            self.forecast = None
            self.learning_error = False
            return {'protocol':1,'control_enabled':False,'summary':self.learning_summary}

    def latest_report(self):
        with self.lock:
            summary = self.report_reader.latest()
            self.report_reader_error = False
            return {'protocol':1,'control_enabled':False,'summary':summary}

    def configure(self, payload):
        profiles = [] if payload == [] else selections(payload)
        with self.lock:
            if self.journal and payload != self.selection:
                raise SafeError('Stop capture before changing selection.')
            if payload != self.selection:
                self.live_observations = {}
                self.shadow.gap();self.shadow.decisions.clear()
            self.profiles, self.selection = profiles, payload
            self.shadow.resume({e for p in profiles for e in p.entities})
        return self.status()

    def context_view(self):
        with self.lock:
            status = self.status()
            allowed = {e for profile in self.profiles for e in profile.entities}
            result = self.learning_summary
            return {'protocol':1,'control_enabled':False,'status':status,'scope':self.selection,
                    'observations':{e:row for e,row in self.live_observations.items() if e in allowed},
                    'active_model_available':bool(result and result.get('entity_id') in allowed),
                    'forecast':self.forecast_view(allowed),
                    'shadow':self.shadow.view(allowed),
                    'archive':dict(self.archive.status),
                    'context_activity_models_available':False,
                    'human_hypotheses_applied_to_models':False}

    def forecast_view(self, allowed):
        if not self.forecast or self.forecast['entity_id'] not in allowed:
            return {'state':'waiting','reason':'Waiting for recent completed hourly statistics from Home Assistant.'}
        result = dict(self.forecast)
        if self.wall()//1000000 >= instant(result['target_end'])//1000000:
            result['state'] = 'stale'
        return result

    def forecast_temperature(self, payload):
        with self.lock:
            allowed = {e for p in self.profiles for e in p.entities}
            if (not isinstance(payload,dict) or payload.keys() != {'entity_id','unit','rows'}
                    or not self.learning_model or payload['entity_id'] not in allowed
                    or payload['entity_id'] != self.learning_model['entity_id'] or payload['unit'] != self.learning_model['unit']):
                raise SafeError('Forecast requires the permitted trained temperature source and matching unit.')
            self.forecast = forecast_window(self.learning_model,payload['rows'],self.wall()//1000000)
            return {'protocol':1,'control_enabled':False,'forecast':self.forecast}

    def start(self, duration=300):
        if type(duration) is not int or not 10 <= duration <= 86400:
            raise SafeError('Invalid capture duration.')
        with self.lock:
            if self.journal or not self.profiles:
                raise SafeError('Configure selection or stop the active capture first.')
            if sum(1 for _ in (self.directory/'captures').iterdir()) >= 64:
                raise SafeError('Capture retention cap reached; archive history locally.')
            self.run_id = 'capture-' + uuid.uuid4().hex
            self.folder = self.directory/'captures'/self.run_id
            self.folder.mkdir(mode=0o700)
            self.journal = Journal(self.folder, 'events')
            write_json(self.folder/'selection.json', {'rooms':self.selection, 'protocol':1})
            self.observer = Observer(self.profiles)
            self.deadline = self.clock() + duration
            self.last_contact = self.clock()
            self.last_sequence, self.last_digest = 0, None
            self.await_snapshot = True
            self.initialized = set()
            self.rows, self.gaps = 0, 0
            self.state = 'capturing'
            self.gap('reconnected')
        return self.status()

    def write(self, row):
        try:
            self.journal.write(row)
        except Exception:
            self.journal.close()
            self.journal = None
            self.state = 'storage_error'
            raise
        self.rows += 1
        # Latest allowlisted projection only; no raw contexts, user IDs or media titles.
        allowed = {'time','room','entity_id','state','value','availability','actor','record_kind','source_time','device_class','unit','attributes','assumed_state'}
        self.live_observations[row['entity_id']] = {k:v for k,v in row.items() if k in allowed}
        self.shadow.on_row(row)

    def gap(self, reason):
        self.shadow.gap()
        for row in self.observer.reset(self.stamp(), reason):
            self.write({**row, 'record_kind':'gap'})
        self.gaps += 1
        self.await_snapshot = True
        self.initialized.clear()

    def ingest(self, payload):
        if (not isinstance(payload, dict) or payload.keys() - {'capture_id','sequence','snapshot','events','refresh'} or not {'capture_id','sequence','snapshot','events'} <= payload.keys()
                or type(payload.get('refresh',False)) is not bool or payload.get('refresh',False) and payload['snapshot']
                or type(payload['sequence']) is not int or not 1 <= payload['sequence'] <= 100000
                or type(payload['snapshot']) is not bool or not isinstance(payload['events'], list)
                or len(payload['events']) > 80):
            raise SafeError('Invalid observation batch.')
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).digest()
        with self.lock:
            self.tick()
            if not self.journal or payload['capture_id'] != self.run_id:
                raise SafeError('No matching active capture.')
            if payload['sequence'] == self.last_sequence and digest == self.last_digest:
                return self.status()
            if payload['sequence'] <= self.last_sequence:
                raise SafeError('Stale observation batch.')
            # Validate every event before changing journal or attribution state.
            for event in payload['events']:
                self.validate_event(event, payload['snapshot'] or payload.get('refresh',False))
            if payload['snapshot'] or payload.get('refresh',False):
                ids = [e['data']['entity_id'] for e in payload['events']]
                if set(ids) != set(self.observer.entities) or len(set(ids)) != len(ids):
                    raise SafeError('A snapshot must include every selected entity exactly once.')
            if not payload['snapshot'] and (self.await_snapshot or payload['sequence'] != self.last_sequence + 1):
                if not self.await_snapshot:
                    self.gap('dropped_events')
                raise SafeError('Fresh snapshot required after a gap.')
            if payload['snapshot'] and not self.await_snapshot:
                self.gap('reconnected')
            for event in payload['events']:
                event = {**event, 'time_fired':self.stamp()}
                row = self.observer.process(event)
                if row:
                    kind = 'snapshot' if payload['snapshot'] or payload.get('refresh',False) else 'removed' if event['data'].get('new_state') is None else 'state_update'
                    selected_state = event['data'].get('new_state') or {}
                    self.write({**row, 'record_kind':kind, 'source_time':selected_state.get('source_time'),
                                'device_class':selected_state.get('device_class'), 'unit':selected_state.get('unit'),'assumed_state':selected_state.get('assumed_state',False)})
                    if row['availability'] == 'reported':
                        self.initialized.add(row['entity_id'])
            self.last_sequence, self.last_digest = payload['sequence'], digest
            self.last_contact = self.clock()
            self.await_snapshot = False
            try:
                self.shadow.sample(self.live_observations,set(self.observer.entities))
            except Exception:
                try:self.shadow.stop()
                except Exception:pass
                self.shadow.error='Shadow journal budget or storage failure; passive capture continues.'
            if self.rows >= 100000:
                self.stop('row_budget')
        return self.status()

    def validate_event(self, event, snapshot):
        if not isinstance(event, dict) or event.keys() != {'event_type', 'data', 'context'}:
            raise SafeError('Invalid observation event.')
        kind, data = event['event_type'], event['data']
        if not isinstance(data, dict) or not isinstance(event['context'], dict):
            raise SafeError('Invalid observation event.')
        entity = data.get('entity_id')
        if kind in {'automation_triggered', 'script_started'} and not snapshot:
            prefix = 'automation.' if kind == 'automation_triggered' else 'script.'
            if data.keys() != {'entity_id'} or not isinstance(entity, str) or not ENTITY_ID.fullmatch(entity) or not entity.startswith(prefix):
                raise SafeError('Invalid attribution event.')
        elif kind == 'state_changed':
            if data.keys() != {'entity_id','new_state'} or entity not in self.observer.entities:
                raise SafeError('Observation outside selected entities.')
            state = data['new_state']
            if state is not None:
                if (not isinstance(state, dict) or not {'state','attributes','context'} <= state.keys()
                        or state.keys() - {'state','attributes','context','source_time','device_class','unit','assumed_state'}
                        or not isinstance(state['state'], str) or len(state['state']) > 64
                        or not isinstance(state['attributes'], dict) or not isinstance(state['context'], dict)
                        or state['attributes'].keys() - {'brightness','color_temp','color_temp_kelvin','temperature','current_temperature','current_position','percentage','hvac_action'}):
                    raise SafeError('Invalid selected state.')
                if 'source_time' in state:
                    instant(state['source_time'])
                if 'device_class' in state and state['device_class'] not in {'temperature','illuminance','humidity','motion','occupancy','presence','power','energy','volume_flow_rate','volume','carbon_dioxide','carbon_monoxide','pm25','pm10','door','window','opening','moisture','running','curtain','shade','blind','shutter','awning','garage','gate','damper'}:
                    raise SafeError('Unsupported observation class.')
                if 'unit' in state and state['unit'] not in {'°C','°F','K','lx','%','W','kW','kWh','Wh','gal/min','L/min','m³/h','gal','L','m³','ppm','µg/m³'}:
                    raise SafeError('Unsupported observation unit.')
                self.validate_context(state['context'])
        else:
            raise SafeError('Unsupported observation event.')
        self.validate_context(event['context'])

    @staticmethod
    def validate_context(context):
        if context.keys() - {'id','parent_id','user_id'} or any(not (v is None or type(v) is bool or isinstance(v, str) and len(v) <= 128) for v in context.values()):
            raise SafeError('Invalid observation context.')

    def tick(self):
        try:self.shadow.expire()
        except Exception:
            self.shadow.state='storage_error';self.shadow.error='Shadow completion journal unavailable; passive capture continues.'
        if not self.journal:
            return
        if self.clock() >= self.deadline:
            self.stop('duration')
        elif self.clock() - self.last_contact > 30 and not self.await_snapshot:
            self.gap('connection_lost')

    def stop(self, reason='user_stopped'):
        with self.lock:
            if not self.journal:
                return self.status()
            journal = self.journal
            try:
                self.gap('connection_lost')
            finally:
                journal.close()
                self.journal = None
                self.state = 'idle'
            report = summarize(load_journals(sorted(self.folder.glob('events-*.jsonl'))), self.profiles)
            report.update(product='DwellMind HA', version=VERSION, stop_reason=reason)
            write_json(self.directory/'reports'/(self.run_id+'.json'), report)
            self.last_report = self.run_id
        return self.status()


def issue_ui_token(master, prefix='ui'):
    body = prefix+'.'+str(int(time.time())+30*86400)+'.'+secrets.token_hex(16)
    signature = hmac.new(master.encode(),body.encode(),hashlib.sha256).hexdigest()
    return body+'.'+signature


def valid_ui_token(supplied, master):
    if not isinstance(supplied,str) or len(supplied)>256 or not supplied.startswith(('Bearer ui.','Bearer ui2.')):
        return False
    token = supplied[7:]
    if not re.fullmatch(r'ui2?\.[0-9]{10,12}\.[0-9a-f]{32}\.[0-9a-f]{64}',token):
        return False
    body, signature = token.rsplit('.',1)
    expiry = int(body.split('.')[1])
    return (int(time.time()) < expiry <= int(time.time())+30*86400
            and hmac.compare_digest(signature,hmac.new(master.encode(),body.encode(),hashlib.sha256).hexdigest()))


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def log_message(self, *args):
        pass  # Requests may carry private selections. Never log headers or bodies.

    def reply(self, status, payload):
        raw = json.dumps(payload, separators=(',', ':'), allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def reply_asset(self):
        name, content_type = UI_ASSETS[self.path]
        try:
            raw = (UI_DIRECTORY/name).read_bytes()
            if len(raw)>131072:
                raise OSError('Static asset exceeds limit.')
        except OSError:
            return self.reply(503,{'error':'Dashboard asset unavailable.'})
        self.send_response(200)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def dispatch(self):
        self.connection.settimeout(5)
        if not private_peer(self.client_address[0]):
            return self.reply(403,{'error':'Private network access required.'})
        if self.command == 'GET' and self.path in UI_ASSETS:
            return self.reply_asset()
        ui_write=self.command=='POST' and self.path in {'/v1/shadow/start','/v1/shadow/stop','/v1/shadow/archive','/v1/shadow/feedback'}
        origin=self.headers.get('Origin')
        same_origin=bool(origin and urlsplit(origin).scheme in {'http','https'} and urlsplit(origin).netloc==self.headers.get('Host') and urlsplit(origin).path in {'','/'})
        if origin is not None and not (ui_write and same_origin):
            return self.reply(403, {'error':'Browser requests are not supported.'})
        if self.command == 'GET' and self.path == '/health':
            return self.reply(200, {'healthy':True})
        supplied = self.headers.get('Authorization', '')
        master = len(supplied)<=256 and hmac.compare_digest(supplied.encode(), ('Bearer '+self.server.token).encode())
        ui_valid=valid_ui_token(supplied,self.server.token)
        readonly = self.command == 'GET' and self.path == '/v1/context' and ui_valid
        contributor=ui_write and same_origin and supplied.startswith('Bearer ui2.') and ui_valid
        if not master and not readonly and not contributor:
            return self.reply(401, {'error':'Pairing key required.'})
        try:
            worker = self.server.worker
            if self.command == 'GET' and self.path == '/v1/ui-session':
                return self.reply(200,{'protocol':1,'control_enabled':False,'credential':issue_ui_token(self.server.token),'expires_in_days':30})
            if self.command=='GET' and self.path=='/v1/ui-workspace-session':
                return self.reply(200,{'protocol':1,'control_enabled':False,'credential':issue_ui_token(self.server.token,'ui2'),'expires_in_days':30})
            if self.command == 'GET' and self.path == '/v1/status':
                return self.reply(200, worker.status())
            if self.command == 'GET' and self.path == '/v1/context':
                return self.reply(200, worker.context_view())
            if self.command == 'GET' and self.path == '/v1/report/latest':
                try:
                    return self.reply(200,worker.latest_report())
                except (SafeError,OSError,ValueError,TypeError):
                    worker.report_reader_error = True
                    return self.reply(503,{'error':'Private capture summary unavailable; history was not modified.'})
            if self.command != 'POST' or self.path not in {'/v1/config','/v1/start','/v1/stop','/v1/events','/v1/learning/train-temperature','/v1/learning/forecast-temperature','/v1/shadow/start','/v1/shadow/stop','/v1/shadow/archive','/v1/shadow/feedback'}:
                return self.reply(404, {'error':'Unknown worker endpoint.'})
            length = self.headers.get('Content-Length','')
            if (self.headers.get('Transfer-Encoding') is not None or not length.isdigit()
                    or not 1 <= int(length) <= (MAX_TRAIN_BODY if self.path == '/v1/learning/train-temperature' else MAX_BODY)
                    or self.headers.get_content_type() != 'application/json'):
                return self.reply(400, {'error':'Bounded JSON body required.'})
            raw = self.rfile.read(int(length))
            if len(raw) != int(length):
                raise SafeError('Incomplete worker request.')
            payload = strict_json(raw)
            if self.path == '/v1/config':
                if not isinstance(payload, dict) or payload.keys() != {'rooms'}:
                    raise SafeError('Invalid selection request.')
                result = worker.configure(payload['rooms'])
            elif self.path == '/v1/start':
                if not isinstance(payload, dict) or payload.keys() != {'duration_seconds'}:
                    raise SafeError('Invalid capture request.')
                result = worker.start(payload['duration_seconds'])
            elif self.path == '/v1/stop':
                if payload != {}:
                    raise SafeError('Invalid stop request.')
                result = worker.stop()
            elif self.path == '/v1/learning/train-temperature':
                result = worker.train_temperature(payload)
            elif self.path == '/v1/learning/forecast-temperature':
                result = worker.forecast_temperature(payload)
            elif self.path == '/v1/shadow/start':
                result = {'protocol':1,'control_enabled':False,'shadow':worker.shadow.start(payload,{e for p in worker.profiles for e in p.entities})}
            elif self.path == '/v1/shadow/stop':
                if payload!={}:raise SafeError('Invalid campaign stop request.')
                worker.shadow.stop();result={'protocol':1,'control_enabled':False}
            elif self.path == '/v1/shadow/archive':
                result={'protocol':1,'control_enabled':False,'archive':worker.archive.start(payload,worker.live_observations)}
            elif self.path == '/v1/shadow/feedback':
                result={'protocol':1,'control_enabled':False,'shadow':worker.shadow.feedback(payload,{e for p in worker.profiles for e in p.entities})}
            else:
                result = worker.ingest(payload)
            self.reply(200, result)
        except (SafeError, ValueError, TypeError, KeyError):
            self.reply(409, {'error':'Worker request refused; review selection or refresh capture state.'})
        except Exception:
            # Stop the observation if a journal/disk budget or unexpected error occurs.
            try:
                self.server.worker.stop('worker_error')
            except Exception:
                pass
            self.reply(503, {'error':'Worker unavailable; inspect local storage and capture status.'})

    do_GET = dispatch
    do_POST = dispatch


class BoundedServer(HTTPServer):
    request_queue_size = 4

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address


def serve(env):
    directory = env.get('DATA_DIR','/data')
    if not Path(directory).is_absolute():
        raise SafeError('Absolute private appdata required.')
    worker = Worker(directory)
    token = service_secret(directory)
    port = int(env.get('SERVICE_PORT','8128'))
    if not 1024 <= port <= 65535:
        raise SafeError('Invalid worker port.')
    # Single request worker and bounded backlog cap CPU/memory and avoid thread floods.
    server = BoundedServer(('0.0.0.0',port), Handler)
    server.timeout = 1
    server.worker, server.token = worker, token
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_:stop.set())
    signal.signal(signal.SIGINT, lambda *_:stop.set())
    print(json.dumps({'product':'DwellMind HA','mode':'service','status':'ready','control_enabled':False}), flush=True)
    try:
        while not stop.is_set():
            server.handle_request()
            worker.tick()
    finally:
        worker.stop('worker_shutdown')
        server.server_close()
    return 0
