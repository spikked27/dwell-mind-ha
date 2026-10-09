"""Bounded read-only projection of existing capture reports. Never read journals/keys."""
import math
import os
from pathlib import Path
import re
import stat

from policy import SafeError, instant, strict_json
from rooms import ENTITY_ID

MAX_REPORT_BYTES = 131072
COUNTS = {'snapshot','state_update','gap','removed'}
ACTORS = {'engine','automation','script','user_associated','unattributed'}


def count(value):
    if type(value) is not int or not 0 <= value <= 100000:
        raise SafeError('Invalid capture report counter.')
    return value


def counters(value, allowed):
    if not isinstance(value,dict) or value.keys() - allowed:
        raise SafeError('Invalid capture report counters.')
    return {k:count(v) for k,v in value.items()}


def project_report(report, report_id):
    if not isinstance(report,dict) or report.get('control_enabled') is not False or report.get('preference_labels') != 0:
        raise SafeError('Not an observation-only capture report.')
    start, end = report.get('start'), report.get('end')
    duration = (instant(end)-instant(start))/1e9
    if not 0 <= duration <= 86500:
        raise SafeError('Invalid capture report interval.')
    entities = report.get('entities')
    if not isinstance(entities,dict) or not 1 <= len(entities) <= 40:
        raise SafeError('Invalid capture report entity count.')
    projected = {}
    for entity, item in entities.items():
        if not isinstance(entity,str) or len(entity)>255 or not ENTITY_ID.fullmatch(entity) or not isinstance(item,dict):
            raise SafeError('Invalid capture report entity.')
        room = item.get('room')
        if not isinstance(room,str) or not 1 <= len(room) <= 64 or any(ord(c)<32 for c in room):
            raise SafeError('Invalid capture report room.')
        coverage = item.get('reported_state_coverage_percent')
        if coverage is not None and (type(coverage) not in {int,float} or not math.isfinite(coverage) or not 0<=coverage<=100):
            raise SafeError('Invalid capture report coverage.')
        projected[entity] = {'room':room,'rows':count(item.get('rows')),'coverage_percent':coverage,
                             'record_kinds':counters(item.get('record_kinds',{}),COUNTS),
                             'light_update_actor_counts':counters(item.get('light_update_actor_counts',{}),ACTORS),
                             'terminal_gap_present':item.get('terminal_gap_present') is True}
    values = [v['coverage_percent'] for v in projected.values()]
    mean = round(sum(values)/len(values),2) if all(v is not None for v in values) else None
    legacy = report.get('capture_stats')
    legacy = legacy if isinstance(legacy,dict) else {}
    stop_reason = report.get('stop_reason',legacy.get('stop_reason','unknown'))
    if not isinstance(stop_reason,str):
        stop_reason = 'unknown'
    if stop_reason not in {'duration','user_stopped','worker_shutdown','worker_error','row_budget','message_budget',
                           'interrupted','authentication_rejected','entity_permission_rejected','reconnect_budget','unknown'}:
        stop_reason = 'unknown'
    return {'report_id':report_id,'start':start,'end':end,'duration_seconds':round(duration,3),
            'stop_reason':stop_reason,'rows':count(report.get('rows')),'entity_count':len(projected),
            'mean_entity_coverage_percent':mean,
            'zero_coverage_entities':[e for e,v in projected.items() if v['coverage_percent']==0],
            'all_entities_have_terminal_gap':report.get('all_entities_have_terminal_gap') is True,
            'entities':projected,'control_enabled':False,'preference_labels':0,
            'scope':'Reported HA-state coverage, not verified human occupancy or physical device uptime.'}


class ReportReader:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.cache_stamp = None
        self.cache = None

    def latest(self):
        stamp = self.directory.stat().st_mtime_ns
        if stamp == self.cache_stamp:
            return self.cache
        latest, latest_time = None, None
        candidates = list(self.directory.glob('capture-*.json'))
        if len(candidates)>64:
            raise SafeError('Report retention exceeds the bounded read limit.')
        for path in candidates:
            if not re.fullmatch(r'capture-[0-9a-f]{32}\.json',path.name):
                continue
            fd = os.open(path,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd,'rb') as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode)&0o077:
                    raise SafeError('Capture report must be a private regular file.')
                raw = handle.read(MAX_REPORT_BYTES+1)
            if len(raw)>MAX_REPORT_BYTES:
                raise SafeError('Capture report exceeds byte budget.')
            summary = project_report(strict_json(raw),path.stem)
            when = instant(summary['end'])
            if latest_time is None or when>latest_time:
                latest, latest_time = summary, when
        self.cache, self.cache_stamp = latest, stamp
        return latest
