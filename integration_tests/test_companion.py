"""Run against pinned real HA dependencies, not stubs of its selector classes."""
from types import SimpleNamespace
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from custom_components.dwellmind.api import WorkerAuthError, WorkerError, validate_connection
from custom_components.dwellmind.button import DwellMindButton
from custom_components.dwellmind.config_flow import DwellMindConfigFlow, entity_schema, room_schema
from custom_components.dwellmind.coordinator import DwellMindCoordinator, state_event
from custom_components.dwellmind.discovery import rooms
from custom_components.dwellmind.sensor import DwellMindSensor


class SchemaTests(unittest.TestCase):
    def test_real_ha_area_and_entity_picker_schemas(self):
        self.assertEqual(room_schema()({'areas':['office']}),{'areas':['office']})
        found={'light.study':'office'}
        result=entity_schema(found,found)({'entities':['light.study'],'capture_seconds':300})
        self.assertEqual(result['entities'],['light.study'])
        with self.assertRaises(Exception):entity_schema(found,found)({'entities':['light.other'],'capture_seconds':300})

    def test_local_http_consent_and_redirect_credential_shapes(self):
        key='dummy-private-key-0000000000000000000000'
        self.assertEqual(validate_connection('https://worker.example',key),'https://worker.example')
        with self.assertRaises(WorkerError):validate_connection('http://worker.example',key)
        self.assertEqual(validate_connection('http://worker.example',key,True),'http://worker.example')
        for url in ['https://user:password@worker.example','https://worker.example/api','https://worker.example?token=x']:
            with self.assertRaises(WorkerError):validate_connection(url,key)

    def test_projection_never_forwards_media_titles_or_user_identity(self):
        state=SimpleNamespace(state='on',attributes={'media_title':'private','brightness':80},
                              context=SimpleNamespace(id='context',parent_id=None,user_id='private-user'))
        event=state_event('media_player.tv',state)
        self.assertNotIn('private',str(event))
        self.assertEqual(event['context']['user_id'],True)


class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_is_fetched_once_per_report_and_visible_to_ha_clients(self):
        coordinator=object.__new__(DwellMindCoordinator)
        coordinator.lock=asyncio.Lock()
        coordinator.report_reference=object()
        coordinator.latest_summary=None
        status={'protocol':1,'control_enabled':False,'room_count':2,'last_report':'capture-example',
                'capabilities':['latest_capture_summary']}
        summary={'report_id':'capture-example','duration_seconds':300,'rows':35,'entities':{}}
        coordinator.client=SimpleNamespace(request=AsyncMock(side_effect=[dict(status),{'summary':summary},dict(status)]))
        first=await coordinator._async_update_data()
        second=await coordinator._async_update_data()
        self.assertEqual(first['latest_summary'],summary)
        self.assertEqual(second['latest_summary'],summary)
        self.assertEqual(coordinator.client.request.await_count,3)
        sensor=DwellMindSensor(SimpleNamespace(data=first),SimpleNamespace(entry_id='example'),'latest_summary','Latest capture report','mdi:file-chart')
        self.assertEqual(sensor.native_value,'available')
        self.assertEqual(sensor.extra_state_attributes['duration_seconds'],300)

    async def test_older_worker_remains_compatible_without_report_endpoint(self):
        coordinator=object.__new__(DwellMindCoordinator);coordinator.lock=asyncio.Lock()
        coordinator.client=SimpleNamespace(request=AsyncMock(return_value={'room_count':2,'control_enabled':False}))
        status=await coordinator._async_update_data()
        sensor=DwellMindSensor(SimpleNamespace(data=status),SimpleNamespace(entry_id='example'),'latest_summary','Latest capture report','mdi:file-chart')
        self.assertEqual(sensor.extra_state_attributes,{'worker_update_required':True})
        self.assertEqual(coordinator.client.request.await_count,1)

    async def test_pair_rooms_review_entities_and_no_automatic_capture(self):
        flow=DwellMindConfigFlow()
        flow.hass=SimpleNamespace()
        flow.async_show_form=lambda **kwargs:{'type':'form',**kwargs}
        flow.async_create_entry=lambda **kwargs:{'type':'create_entry',**kwargs}
        found={'light.study':'office'}
        selected=[{'area_id':'office','name':'Office','entities':['light.study']}]
        client=SimpleNamespace(url='https://worker.example',key='dummy-pairing-key-000000000000000000',request=AsyncMock(return_value={'protocol':1,'control_enabled':False}))
        with patch('custom_components.dwellmind.config_flow.WorkerClient',return_value=client), \
             patch('custom_components.dwellmind.config_flow.async_get_clientsession',return_value=object()), \
             patch.object(flow,'_async_current_entries',return_value=[]), \
             patch('custom_components.dwellmind.config_flow.candidates',return_value=found), \
             patch('custom_components.dwellmind.config_flow.rooms',return_value=selected):
            result=await flow.async_step_user({'url':client.url,'pairing_key':'dummy-pairing-key-000000000000000000','allow_http':False})
            self.assertEqual(result['step_id'],'rooms')
            result=await flow.async_step_rooms({'areas':['office']})
            self.assertEqual(result['step_id'],'entities')
            result=await flow.async_step_entities({'entities':['light.study'],'capture_seconds':300})
            self.assertEqual(result['type'],'create_entry')
            self.assertEqual(result['options']['areas'],['office'])
            self.assertEqual(client.request.await_args_list[-1].args,('POST','/v1/config',{'rooms':selected}))
            self.assertFalse(any(call.args[1]=='/v1/start' for call in client.request.await_args_list))

    async def test_failed_pairing_does_not_create_entry(self):
        flow=DwellMindConfigFlow();flow.hass=SimpleNamespace()
        flow.async_show_form=lambda **kwargs:kwargs
        client=SimpleNamespace(request=AsyncMock(side_effect=WorkerAuthError('Rejected')))
        with patch('custom_components.dwellmind.config_flow.WorkerClient',return_value=client), \
             patch('custom_components.dwellmind.config_flow.async_get_clientsession',return_value=object()):
            result=await flow.async_step_user({'url':'https://worker.example','pairing_key':'bad','allow_http':False})
        self.assertEqual(result['errors'],{'base':'invalid_auth'})
