"""Home Assistant companion for the local DwellMind Unraid worker."""
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import WorkerClient
from .const import PLATFORMS
from .coordinator import DwellMindCoordinator
from .services import register_services


async def async_setup_entry(hass, entry):
    client = WorkerClient(async_get_clientsession(hass),entry.data['url'],entry.data['pairing_key'],entry.data['allow_http'])
    coordinator = DwellMindCoordinator(hass,entry,client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry,PLATFORMS)
    coordinator.begin_listening()
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry,PLATFORMS):
        await entry.runtime_data.close()
        return True
    return False
