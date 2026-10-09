"""Native HA setup: worker pairing, rooms, then a reviewable entity picker."""
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from .api import WorkerAuthError, WorkerClient, WorkerError
from .const import DOMAIN
from .discovery import candidates, rooms


def room_schema(default=()):
    return vol.Schema({vol.Required('areas', default=list(default)):
                       selector.AreaSelector(selector.AreaSelectorConfig(multiple=True))})


def entity_schema(found, default):
    return vol.Schema({vol.Required('entities', default=list(default)):
                       selector.EntitySelector(selector.EntitySelectorConfig(multiple=True, include_entities=list(found))),
                       vol.Required('capture_seconds', default=300):
                       selector.NumberSelector(selector.NumberSelectorConfig(min=10,max=86400,mode='box',unit_of_measurement='s'))})


class DwellMindConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self.connection = {}
        self.area_ids = []

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                client = WorkerClient(async_get_clientsession(self.hass), user_input['url'], user_input['pairing_key'], user_input['allow_http'])
                await client.request('GET','/v1/status')
                if any(e.data.get('url') == client.url for e in self._async_current_entries()):
                    return self.async_abort(reason='already_configured')
                self.connection = {**user_input, 'url':client.url, 'pairing_key':client.key}
                return await self.async_step_rooms()
            except WorkerAuthError:
                errors['base'] = 'invalid_auth'
            except WorkerError as error:
                errors['base'] = error.code
        shown_url = user_input.get('url','') if user_input else ''
        shown_http = user_input.get('allow_http',False) if user_input else False
        return self.async_show_form(step_id='user', errors=errors, data_schema=vol.Schema({
            vol.Required('url', default=shown_url):selector.TextSelector(selector.TextSelectorConfig(type='url')),
            vol.Required('pairing_key'):selector.TextSelector(selector.TextSelectorConfig(type='password')),
            vol.Required('allow_http', default=shown_http):selector.BooleanSelector()}))

    async def async_step_rooms(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                found = candidates(self.hass, user_input['areas'])
                if not found:
                    raise ValueError('No candidates')
                self.area_ids = user_input['areas']
                return await self.async_step_entities()
            except ValueError:
                errors['base'] = 'no_entities'
        return self.async_show_form(step_id='rooms', errors=errors, data_schema=room_schema(self.area_ids))

    async def async_step_entities(self, user_input=None):
        found = candidates(self.hass, self.area_ids)
        errors = {}
        if user_input is not None:
            try:
                selected = rooms(self.hass, self.area_ids, user_input['entities'])
                duration = user_input['capture_seconds']
                if not 10 <= duration <= 86400 or int(duration) != duration:
                    raise ValueError('Invalid duration')
                client = WorkerClient(async_get_clientsession(self.hass), self.connection['url'], self.connection['pairing_key'], self.connection['allow_http'])
                await client.request('POST','/v1/config',{'rooms':selected})
                return self.async_create_entry(title='DwellMind HA', data=self.connection,
                                              options={'areas':self.area_ids,'entities':user_input['entities'],'capture_seconds':int(duration)})
            except ValueError:
                errors['base'] = 'invalid_selection'
            except WorkerError:
                errors['base'] = 'cannot_connect'
        return self.async_show_form(step_id='entities', errors=errors,
                                   data_schema=entity_schema(found, found if len(found) <= 40 else []))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return DwellMindOptionsFlow()


class DwellMindOptionsFlow(config_entries.OptionsFlow):
    def __init__(self):
        self.area_ids = []

    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                if not candidates(self.hass, user_input['areas']):
                    raise ValueError('No candidates')
                self.area_ids = user_input['areas']
                return await self.async_step_entities()
            except ValueError:
                errors['base'] = 'no_entities'
        return self.async_show_form(step_id='init', errors=errors,
                                   data_schema=room_schema(self.config_entry.options.get('areas',[])))

    async def async_step_entities(self, user_input=None):
        found = candidates(self.hass, self.area_ids)
        errors = {}
        if user_input is not None:
            try:
                selected = rooms(self.hass, self.area_ids, user_input['entities'])
                duration = user_input['capture_seconds']
                if not 10 <= duration <= 86400 or int(duration) != duration:
                    raise ValueError('Invalid duration')
                data = self.config_entry.data
                client = WorkerClient(async_get_clientsession(self.hass),data['url'],data['pairing_key'],data['allow_http'])
                await client.request('POST','/v1/config',{'rooms':selected})
                return self.async_create_entry(title='',data={'areas':self.area_ids,'entities':user_input['entities'],'capture_seconds':int(duration)})
            except ValueError:
                errors['base'] = 'invalid_selection'
            except WorkerError:
                errors['base'] = 'cannot_connect'
        # Newly selected areas get their discovered entities; existing exclusions persist.
        defaults = [e for e in self.config_entry.options.get('entities',[]) if e in found]
        return self.async_show_form(step_id='entities',errors=errors,data_schema=entity_schema(found,defaults or list(found) if len(found)<=40 else defaults))
