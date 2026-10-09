"""Lightweight HA bridge. Local work stays on Unraid; HA forwards selected data."""
import asyncio
from collections import deque
from datetime import timedelta
import logging
import math

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import WorkerError
from .const import DOMAIN
from .discovery import rooms

LOGGER = logging.getLogger(__name__)


def context(value):
    if value is None:
        return {}
    return {'id':value.id, 'parent_id':value.parent_id, 'user_id':bool(value.user_id)}


def state_event(entity_id, state):
    if state is None:
        new = None
        ctx = {}
    else:
        ctx = context(state.context)
        new = {'state':state.state if len(state.state)<=64 else 'other',
               'attributes':{k:v for k,v in state.attributes.items() if k in {'brightness','color_temp','color_temp_kelvin'}
                             and type(v) in {int,float} and math.isfinite(v)}, 'context':ctx}
        if getattr(state,'last_updated',None) is not None:
            new['source_time'] = state.last_updated.isoformat()
        device_class = state.attributes.get('device_class')
        if device_class in {'temperature','illuminance','humidity','motion','occupancy','presence'}:
            new['device_class'] = device_class
        unit = state.attributes.get('unit_of_measurement')
        if unit in {'°C','°F','K','lx','%'}:
            new['unit'] = unit
    return {'event_type':'state_changed', 'context':ctx, 'data':{'entity_id':entity_id, 'new_state':new}}


class DwellMindCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, client):
        super().__init__(hass,LOGGER,name='DwellMind HA',config_entry=entry,update_interval=timedelta(seconds=15))
        self.entry, self.client = entry, client
        self.queue = deque()
        self.lock = asyncio.Lock()
        self.capture_id = None
        self.sequence = 0
        self.need_snapshot = True
        self.overflow = False
        self.unsubscribers = []
        self.report_reference = object()
        self.latest_summary = None
        self.selection = rooms(hass,entry.options['areas'],entry.options['entities'])
        self.entities = frozenset(entry.options['entities'])

    async def _async_setup(self):
        try:
            await self.client.request('POST','/v1/config',{'rooms':self.selection})
        except WorkerError:
            raise UpdateFailed('Worker configuration refused; stop any active capture before changing rooms.') from None

    async def _async_update_data(self):
        try:
            async with self.lock:
                status = await self.client.request('GET','/v1/status')
                if status['room_count'] == 0:
                    status = await self.client.request('POST','/v1/config',{'rooms':self.selection})
                if 'latest_capture_summary' in status.get('capabilities',[]):
                    reference = status.get('last_report')
                    if reference != self.report_reference or status.get('report_reader_error'):
                        try:
                            report = await self.client.request('GET','/v1/report/latest')
                            self.latest_summary = report.get('summary')
                            self.report_reference = reference
                        except WorkerError:
                            status['report_reader_error'] = True
                    status['latest_summary'] = self.latest_summary
                return status
        except WorkerError:
            self.need_snapshot = True
            self.queue.clear()
            raise UpdateFailed('Worker unavailable; observation coverage is unknown.') from None

    @callback
    def begin_listening(self):
        self.unsubscribers.append(self.hass.bus.async_listen(EVENT_STATE_CHANGED,self.on_state))
        for kind in ('automation_triggered','script_started'):
            self.unsubscribers.append(self.hass.bus.async_listen(kind,self.on_cause))
        self.unsubscribers.append(async_track_time_interval(self.hass,self.flush,timedelta(seconds=2)))

    @callback
    def enqueue(self, event):
        if not self.data or self.data.get('state') != 'capturing':
            return
        if len(self.queue) >= 400:
            self.queue.clear()
            self.overflow = True
        self.queue.append(event)

    @callback
    def on_state(self, event):
        entity_id = event.data.get('entity_id')
        if entity_id in self.entities:
            self.enqueue(state_event(entity_id,event.data.get('new_state')))

    @callback
    def on_cause(self, event):
        source = event.data.get('entity_id')
        prefix = 'automation.' if event.event_type == 'automation_triggered' else 'script.'
        if isinstance(source,str) and source.startswith(prefix):
            self.enqueue({'event_type':event.event_type,'context':context(event.context),'data':{'entity_id':source}})

    async def flush(self, _now=None):
        if self.lock.locked():
            return
        async with self.lock:
            if not self.data or self.data.get('state') != 'capturing':
                self.queue.clear()
                self.need_snapshot = True
                return
            capture_id = self.data['capture_id']
            if self.capture_id != capture_id:
                self.capture_id, self.sequence = capture_id, 0
                self.need_snapshot = True
            snapshot = self.need_snapshot or self.overflow
            if snapshot:
                self.queue.clear()
                events = [state_event(e,self.hass.states.get(e)) for e in sorted(self.entities)]
            else:
                events = [self.queue.popleft() for _ in range(min(80,len(self.queue)))]
            self.sequence += 1
            try:
                status = await self.client.request('POST','/v1/events',{'capture_id':capture_id,
                        'sequence':self.sequence,'snapshot':snapshot,'events':events})
                self.need_snapshot = self.overflow = False
                status['latest_summary'] = self.latest_summary
                self.async_set_updated_data(status)
            except WorkerError:
                self.need_snapshot = True
                self.queue.clear()
                # Poll discovers completed/restarted jobs; never replay lost transitions as corrections.
                self.async_set_update_error(UpdateFailed('Observation delivery interrupted; fresh snapshot required.'))

    async def start_capture(self, duration=None):
        duration = self.entry.options['capture_seconds'] if duration is None else duration
        if type(duration) is not int or not 10 <= duration <= 86400:
            raise UpdateFailed('Capture duration must be 10 to 86400 seconds.')
        async with self.lock:
            try:
                status = await self.client.request('POST','/v1/config',{'rooms':self.selection})
                status = await self.client.request('POST','/v1/start',{'duration_seconds':duration})
                status['latest_summary'] = self.latest_summary
                self.async_set_updated_data(status)
            except WorkerError:
                raise UpdateFailed('Cannot start observation capture.') from None
        await self.flush()

    async def stop_capture(self):
        async with self.lock:
            try:
                status = await self.client.request('POST','/v1/stop',{})
                status['latest_summary'] = self.latest_summary
                self.async_set_updated_data(status)
            except WorkerError:
                raise UpdateFailed('Cannot stop worker capture; it remains bounded by its timer.') from None

    async def close(self):
        for unsubscribe in self.unsubscribers:
            unsubscribe()
        self.unsubscribers.clear()
        self.queue.clear()
        if self.data and self.data.get('state') == 'capturing':
            try:
                await self.stop_capture()
            except UpdateFailed:
                pass
