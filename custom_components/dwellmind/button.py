"""Explicit bounded observation controls. No home device controls."""
from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity, UpdateFailed

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([DwellMindButton(entry.runtime_data,entry,True),DwellMindButton(entry.runtime_data,entry,False)])


class DwellMindButton(CoordinatorEntity, ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, start):
        super().__init__(coordinator)
        self.start = start
        self._attr_name = 'Start observation capture' if start else 'Stop observation capture'
        self._attr_icon = 'mdi:play' if start else 'mdi:stop'
        self._attr_unique_id = entry.entry_id+('_start' if start else '_stop')
        self._attr_device_info = {'identifiers':{(DOMAIN,entry.entry_id)}}

    async def async_press(self):
        try:
            if self.start:
                await self.coordinator.start_capture()
            else:
                await self.coordinator.stop_capture()
        except UpdateFailed:
            raise HomeAssistantError('DwellMind worker request failed; check connection and capture status.') from None

