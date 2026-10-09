"""Lightweight HA bridge. Local work stays on Unraid; HA forwards selected data."""
import asyncio
from collections import deque
from datetime import timedelta
from datetime import datetime, timezone
from functools import partial
import logging
import math

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
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
        self.selection = self.reviewed_scope()
        self.entities = frozenset(e for room in self.selection for e in room['entities'])
        self.scope_dirty = False
        self.forecast_hour = None
        self.forecast_retry = None

    async def update_forecast(self, status):
        result = status.get('learning_summary')
        if not result or 'temperature_live_forecast' not in status.get('capabilities',[]):
            return
        entity = result['entity_id']
        state = self.hass.states.get(entity)
        end = datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
        identity = (result.get('model_id'),end)
        now = datetime.now(timezone.utc)
        if (identity == self.forecast_hour or self.scope_dirty or entity not in self.entities
                or self.forecast_retry is not None and now < self.forecast_retry
                or state is None or state.state in {'unknown','unavailable'}
                or state.attributes.get('device_class') != 'temperature'
                or state.attributes.get('unit_of_measurement') != result['unit']):
            return
        # Read only 27 completed hours for the already reviewed trained source.
        self.forecast_retry = now+timedelta(minutes=5)
        try:
            data = await get_instance(self.hass).async_add_executor_job(partial(statistics_during_period,
                self.hass,end-timedelta(hours=27),end,{entity},'hour',{'temperature':result['unit']},{'mean'}))
        except Exception:
            LOGGER.warning('Forecast statistics unavailable; passive capture continues.')
            return
        if self.scope_dirty or entity not in self.entities:
            return
        rows = [{'start':int(r['start']*1000),'mean':r.get('mean')} for r in data.get(entity,[])
                if (end-timedelta(hours=27)).timestamp() <= r['start'] < end.timestamp()]
        try:
            await self.client.request('POST','/v1/learning/forecast-temperature',{'entity_id':entity,'unit':result['unit'],'rows':rows})
        except WorkerError:
            # Statistics can arrive late. Retry in five minutes without blocking capture.
            return
        self.forecast_hour = identity

    def reviewed_scope(self):
        return rooms(self.hass,self.entry.options['areas'],self.entry.options['entities'],
                     self.entry.options.get('excluded_areas',()),self.entry.options.get('excluded_entities',()),retain_review=True)

    async def refresh_scope(self):
        # Caller owns self.lock. Never send queued rows while membership is stale.
        if not getattr(self,'scope_dirty',False):
            return
        selection = self.reviewed_scope()
        if selection != self.selection:
            status = await self.client.request('GET','/v1/status')
            if status.get('state') == 'capturing':
                await self.client.request('POST','/v1/stop',{})
            status = await self.client.request('POST','/v1/config',{'rooms':selection})
            self.selection = selection
            self.entities = frozenset(e for room in selection for e in room['entities'])
            self.queue.clear()
            self.need_snapshot = True
            status['latest_summary'] = self.latest_summary
            self.async_set_updated_data(self.scope_status(status))
        self.scope_dirty = False

    def scope_status(self, status):
        status['scope_paused'] = not self.selection
        result = status.get('learning_summary')
        if result and result.get('entity_id') not in self.entities:
            status['learning_scope_blocked'] = True
            # Historical result stays on Unraid; don't expose it as a usable model.
            status['learning_summary'] = None
        return status

    async def _async_setup(self):
        try:
            if self.entry.options.get('excluded_areas') or self.entry.options.get('excluded_entities'):
                status = await self.client.request('GET','/v1/status')
                if 'explicit_exclusions' not in status.get('capabilities',[]):
                    await self.client.request('POST','/v1/stop',{})
                    raise UpdateFailed('Update the worker before loading explicit exclusions.')
            await self.client.request('POST','/v1/config',{'rooms':self.selection})
        except WorkerError:
            raise UpdateFailed('Worker configuration refused; stop any active capture before changing rooms.') from None

    async def _async_update_data(self):
        try:
            async with self.lock:
                await self.refresh_scope()
                status = await self.client.request('GET','/v1/status')
                if status['room_count'] == 0 and self.selection:
                    status = await self.client.request('POST','/v1/config',{'rooms':self.selection})
                await self.update_forecast(status)
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
                return self.scope_status(status)
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
        for kind in ('entity_registry_updated','device_registry_updated','area_registry_updated'):
            self.unsubscribers.append(self.hass.bus.async_listen(kind,self.invalidate_scope))

    @callback
    def invalidate_scope(self, _event):
        self.scope_dirty = True
        self.queue.clear()
        self.need_snapshot = True

    @callback
    def enqueue(self, event):
        if self.scope_dirty or not self.data or self.data.get('state') != 'capturing':
            return
        if len(self.queue) >= 400:
            self.queue.clear()
            self.overflow = True
        self.queue.append(event)

    @callback
    def on_state(self, event):
        entity_id = event.data.get('entity_id')
        if entity_id in self.entities:
            old, new = event.data.get('old_state'), event.data.get('new_state')
            if (self.entry.options.get('excluded_areas') or self.entry.options.get('excluded_entities')) and (
                    (old.attributes.get('entity_id') if old else None) != (new.attributes.get('entity_id') if new else None)):
                self.invalidate_scope(event)
                return
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
            try:
                await self.refresh_scope()
            except WorkerError:
                self.async_set_update_error(UpdateFailed('Scope update failed; no further observations are sent.'))
                return
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
                self.async_set_updated_data(self.scope_status(status))
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
                await self.refresh_scope()
                if not self.selection:
                    raise UpdateFailed('Observation is paused because no reviewed entities remain allowed.')
                status = await self.client.request('POST','/v1/config',{'rooms':self.selection})
                status = await self.client.request('POST','/v1/start',{'duration_seconds':duration})
                status['latest_summary'] = self.latest_summary
                self.async_set_updated_data(self.scope_status(status))
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
