"""Native HA observation status entities, never actuation or household payloads."""
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

FIELDS = [('state','Worker status','mdi:brain'),('entity_count','Selected entities','mdi:format-list-checks'),
          ('room_count','Selected rooms','mdi:floor-plan'),('gaps','Capture gaps','mdi:connection'),
          ('rows','Observation rows','mdi:chart-timeline-variant'),('latest_summary','Latest capture report','mdi:file-chart'),
          ('learning_summary','Temperature learning result','mdi:chart-bell-curve')]


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([DwellMindSensor(entry.runtime_data,entry,key,name,icon) for key,name,icon in FIELDS])


class DwellMindSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, key, name, icon):
        super().__init__(coordinator)
        self.key = key
        self._attr_name, self._attr_icon = name, icon
        self._attr_unique_id = entry.entry_id+'_'+key
        self._attr_device_info = {'identifiers':{(DOMAIN,entry.entry_id)},'name':'DwellMind HA',
                                  'manufacturer':'DwellMind HA','model':'Unraid observation worker'}

    @property
    def native_value(self):
        if self.key == 'learning_summary':
            if self.coordinator.data.get('learning_error'):
                return 'result_unavailable'
            result = self.coordinator.data.get(self.key)
            return result.get('evaluation_status') if result else 'not_trained'
        if self.key == 'latest_summary':
            if self.coordinator.data.get('report_reader_error'):
                return 'report_unavailable'
            return 'available' if self.coordinator.data.get(self.key) else 'not_available'
        return self.coordinator.data.get(self.key)

    @property
    def extra_state_attributes(self):
        if self.key == 'learning_summary':
            return self.coordinator.data.get(self.key) or {'worker_update_required':
                'temperature_forecast_training' not in self.coordinator.data.get('capabilities', [])}
        if self.key == 'latest_summary':
            return self.coordinator.data.get(self.key) or {'worker_update_required':
                'latest_capture_summary' not in self.coordinator.data.get('capabilities',[])}
        return None
