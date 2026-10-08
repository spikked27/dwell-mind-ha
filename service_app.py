"""Authenticated local observation worker. No HA credentials or control API."""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import threading
import time
import uuid

from container_app import VERSION, private_directory, write_json
from live_report import load_journals, summarize
from office_collect import rfc3339_ns
from passive_observer import Observer
from policy import SafeError, instant, strict_json
from private_journal import Journal
from rooms import ENTITY_ID
from upstream import read_secret

MAX_BODY = 131072
MAX_ENTITIES = 40


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
            if not isinstance(entity, str) or not ENTITY_ID.fullmatch(entity) or entity.split('.')[0] not in {'light', 'binary_sensor', 'sensor', 'media_player'}:
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
        for path in (self.directory, self.directory/'captures', self.directory/'reports'):
            private_directory(path)
        self.clock, self.wall = clock, wall
        self.lock = threading.RLock()
        self.profiles, self.selection = [], []
        self.journal = None
        self.run_id = None
        self.state = 'idle'
        self.last_report = None
        self.gaps = 0
        self.rows = 0
        self.last_stamp = 0
        self.last_sequence = 0
        self.last_digest = None
        self.last_contact = 0
        self.await_snapshot = True
        self.initialized = set()

    def stamp(self):
        self.last_stamp = max(self.wall(), self.last_stamp + 1)
        return rfc3339_ns(self.last_stamp)

    def status(self):
        with self.lock:
            self.tick()
            return {'product':'DwellMind HA', 'version':VERSION, 'protocol':1,
                    'state':self.state, 'capture_id':self.run_id,
                    'room_count':len(self.profiles), 'entity_count':sum(len(p.entities) for p in self.profiles),
                    'rows':self.rows, 'gaps':self.gaps, 'initialized_entities':len(self.initialized),
                    'last_report':self.last_report, 'control_enabled':False}

    def configure(self, payload):
        profiles = selections(payload)
        with self.lock:
            if self.journal and payload != self.selection:
                raise SafeError('Stop capture before changing selection.')
            self.profiles, self.selection = profiles, payload
        return self.status()

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

    def gap(self, reason):
        for row in self.observer.reset(self.stamp(), reason):
            self.write({**row, 'record_kind':'gap'})
        self.gaps += 1
        self.await_snapshot = True
        self.initialized.clear()

    def ingest(self, payload):
        if (not isinstance(payload, dict) or payload.keys() != {'capture_id','sequence','snapshot','events'}
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
                self.validate_event(event, payload['snapshot'])
            if payload['snapshot']:
                ids = [e['data']['entity_id'] for e in payload['events']]
                if set(ids) != set(self.observer.entities) or len(set(ids)) != len(ids):
                    raise SafeError('A snapshot must include every selected entity exactly once.')
            elif self.await_snapshot or payload['sequence'] != self.last_sequence + 1:
                if not self.await_snapshot:
                    self.gap('dropped_events')
                raise SafeError('Fresh snapshot required after a gap.')
            if payload['snapshot'] and not self.await_snapshot:
                self.gap('reconnected')
            for event in payload['events']:
                event = {**event, 'time_fired':self.stamp()}
                row = self.observer.process(event)
                if row:
                    kind = 'snapshot' if payload['snapshot'] else 'removed' if event['data'].get('new_state') is None else 'state_update'
                    source_time = (event['data'].get('new_state') or {}).get('source_time')
                    self.write({**row, 'record_kind':kind, 'source_time':source_time})
                    if row['availability'] == 'reported':
                        self.initialized.add(row['entity_id'])
            self.last_sequence, self.last_digest = payload['sequence'], digest
            self.last_contact = self.clock()
            self.await_snapshot = False
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
                        or state.keys() - {'state','attributes','context','source_time'}
                        or not isinstance(state['state'], str) or len(state['state']) > 64
                        or not isinstance(state['attributes'], dict) or not isinstance(state['context'], dict)
                        or state['attributes'].keys() - {'brightness','color_temp','color_temp_kelvin'}):
                    raise SafeError('Invalid selected state.')
                if 'source_time' in state:
                    instant(state['source_time'])
                self.validate_context(state['context'])
        else:
            raise SafeError('Unsupported observation event.')
        self.validate_context(event['context'])

    @staticmethod
    def validate_context(context):
        if context.keys() - {'id','parent_id','user_id'} or any(not (v is None or type(v) is bool or isinstance(v, str) and len(v) <= 128) for v in context.values()):
            raise SafeError('Invalid observation context.')

    def tick(self):
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

    def dispatch(self):
        self.connection.settimeout(5)
        if self.headers.get('Origin') is not None:
            return self.reply(403, {'error':'Browser requests are not supported.'})
        if self.command == 'GET' and self.path == '/health':
            return self.reply(200, {'healthy':True})
        supplied = self.headers.get('Authorization', '')
        if len(supplied) > 256 or not hmac.compare_digest(supplied.encode(), ('Bearer '+self.server.token).encode()):
            return self.reply(401, {'error':'Pairing key required.'})
        try:
            worker = self.server.worker
            if self.command == 'GET' and self.path == '/v1/status':
                return self.reply(200, worker.status())
            if self.command != 'POST' or self.path not in {'/v1/config','/v1/start','/v1/stop','/v1/events'}:
                return self.reply(404, {'error':'Unknown worker endpoint.'})
            length = self.headers.get('Content-Length','')
            if (self.headers.get('Transfer-Encoding') is not None or not length.isdigit()
                    or not 1 <= int(length) <= MAX_BODY or self.headers.get_content_type() != 'application/json'):
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
