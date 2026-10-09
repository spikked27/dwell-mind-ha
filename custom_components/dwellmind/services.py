"""Explicit bounded observation actions; no access to home device controls."""
import voluptuous as vol
from datetime import datetime, timedelta, timezone
from functools import partial
from zoneinfo import ZoneInfo
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import DOMAIN
from .api import WorkerError


def register_services(hass):
    def selected_entry(call):
        entries = [entry for entry in hass.config_entries.async_entries(DOMAIN)
                   if getattr(entry, 'runtime_data', None) is not None
                   and (not call.data.get('entry_id') or entry.entry_id == call.data['entry_id'])]
        if len(entries) != 1:
            raise HomeAssistantError('Select one loaded DwellMind integration with entry_id.')
        return entries[0]

    async def start(call):
        entry = selected_entry(call)
        try:
            await entry.runtime_data.start_capture(call.data['duration_seconds'])
        except UpdateFailed:
            raise HomeAssistantError('Cannot start bounded observation; check worker status.') from None

    hass.services.async_register(DOMAIN, 'start_capture', start, schema=vol.Schema({
        vol.Required('duration_seconds'): vol.All(vol.Coerce(int), vol.Range(min=10, max=86400)),
        vol.Optional('entry_id'): str,
    }))

    async def start_shadow(call):
        entry=selected_entry(call);coordinator=entry.runtime_data
        try:
            async with coordinator.lock:
                await coordinator.refresh_scope()
                if 'shadow_campaign' not in coordinator.data.get('capabilities',[]):
                    raise HomeAssistantError('Update the Unraid worker before starting the shadow campaign.')
                move=datetime.fromisoformat(call.data['move_date']).replace(tzinfo=ZoneInfo(hass.config.time_zone))
                await coordinator.client.request('POST','/v1/shadow/start',{'days':call.data['days'],'move_date':move.isoformat(),'timezone':hass.config.time_zone})
            if coordinator.data.get('state')!='capturing':await coordinator.start_capture(86400)
            await coordinator.async_request_refresh()
        except (WorkerError,UpdateFailed,ValueError):
            raise HomeAssistantError('Shadow campaign could not start; check worker status and reviewed scope.') from None

    async def stop_shadow(call):
        coordinator=selected_entry(call).runtime_data
        try:
            await coordinator.client.request('POST','/v1/shadow/stop',{})
            await coordinator.async_request_refresh()
        except WorkerError:
            raise HomeAssistantError('Cannot reach worker to stop shadow campaign.') from None

    hass.services.async_register(DOMAIN,'start_shadow_campaign',start_shadow,schema=vol.Schema({
        vol.Required('move_date'):str,vol.Optional('days',default=14):vol.All(vol.Coerce(int),vol.Range(min=1,max=30)),vol.Optional('entry_id'):str}))
    hass.services.async_register(DOMAIN,'stop_shadow_campaign',stop_shadow,schema=vol.Schema({vol.Optional('entry_id'):str}))

    async def train_temperature(call):
        entry = selected_entry(call)
        coordinator = entry.runtime_data
        entity = call.data['entity_id']
        state = hass.states.get(entity)
        if (entity not in coordinator.entities or state is None
                or state.attributes.get('device_class') != 'temperature'
                or state.attributes.get('unit_of_measurement') not in {'°C','°F'}):
            raise HomeAssistantError('Choose a reviewed temperature entity with an explicit unit.')
        if 'temperature_forecast_training' not in coordinator.data.get('capabilities', []):
            raise HomeAssistantError('Update the Unraid worker before historical training.')
        try:
            end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
            start = datetime.fromisoformat(call.data['start_date']).replace(tzinfo=timezone.utc)
            move = datetime.fromisoformat(call.data['move_date']).replace(tzinfo=ZoneInfo(hass.config.time_zone))
            if not start < move < end or end-start > timedelta(days=5*366):
                raise ValueError('Invalid history bounds')
        except (ValueError, TypeError):
            raise HomeAssistantError('Use ISO dates with a move date inside a maximum five-year window.') from None
        if getattr(coordinator, 'training_active', False):
            raise HomeAssistantError('A historical import is already active.')
        coordinator.training_active = True
        try:
            rows = []
            cursor = start
            unit = state.attributes['unit_of_measurement']
            recorder = get_instance(hass)
            while cursor < end:
                if getattr(coordinator,'scope_dirty',False) or entity not in coordinator.entities:
                    raise HomeAssistantError('Scope changed; historical import stopped before training.')
                # Monthly read-only Recorder helper calls, off the event loop.
                finish = min(end, (cursor.replace(day=28)+timedelta(days=4)).replace(day=1))
                data = await recorder.async_add_executor_job(partial(statistics_during_period,
                    hass, cursor, finish, {entity}, 'hour', {'temperature':unit}, {'mean'}))
                for row in data.get(entity, []):
                    timestamp = int(row['start']*1000)
                    if cursor.timestamp()*1000 <= timestamp < finish.timestamp()*1000:
                        rows.append({'start':timestamp, 'mean':row.get('mean')})
                if len(rows) > 45000:
                    raise HomeAssistantError('Historical import exceeds the bounded row budget.')
                cursor = finish
            async with coordinator.lock:
                if getattr(coordinator,'scope_dirty',False) or entity not in coordinator.entities:
                    raise HomeAssistantError('Scope changed; historical import stopped before training.')
                response = await coordinator.client.request('POST','/v1/learning/train-temperature',{
                    'entity_id':entity, 'unit':unit, 'start':start.isoformat(), 'end':end.isoformat(),
                    'move_date':move.isoformat(), 'rows':rows})
                status = dict(coordinator.data)
                status['learning_summary'] = response['summary']
                status['learning_error'] = False
                coordinator.async_set_updated_data(status)
        except WorkerError:
            raise HomeAssistantError('Worker training request failed; prior history and models are retained.') from None
        finally:
            coordinator.training_active = False

    hass.services.async_register(DOMAIN, 'train_temperature_forecast', train_temperature, schema=vol.Schema({
        vol.Required('entity_id'): str, vol.Required('move_date'): str,
        vol.Required('start_date'): str, vol.Optional('entry_id'): str,
    }))
