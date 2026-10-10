"""Run against pinned real HA dependencies, not stubs of its selector classes."""
from types import SimpleNamespace
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from custom_components.dwellmind.api import WorkerAuthError, WorkerError, validate_connection
from custom_components.dwellmind.button import DwellMindButton
from custom_components.dwellmind.config_flow import DwellMindConfigFlow, DwellMindOptionsFlow, entity_schema, room_schema
from custom_components.dwellmind.coordinator import DwellMindCoordinator, state_event
from custom_components.dwellmind.discovery import rooms
from custom_components.dwellmind.sensor import DwellMindSensor
from custom_components.dwellmind.services import register_services


class OngoingForecastTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_scoped_hourly_import_and_gap_retry(self):
        from datetime import datetime, timedelta, timezone
        c=object.__new__(DwellMindCoordinator)
        c.forecast_hour=c.forecast_retry=None;c.scope_dirty=False
        c.entities={'sensor.study_temperature'}
        c.hass=SimpleNamespace(states=SimpleNamespace(get=lambda _:SimpleNamespace(state='70',attributes={'device_class':'temperature','unit_of_measurement':'°F'})))
        c.client=SimpleNamespace(request=AsyncMock())
        status={'capabilities':['temperature_live_forecast'],'learning_summary':{'entity_id':'sensor.study_temperature','unit':'°F','model_id':'synthetic'}}
        end=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
        rows=[{'start':(end-timedelta(hours=i)).timestamp(),'mean':70} for i in range(27,0,-1)]
        recorder=SimpleNamespace(async_add_executor_job=AsyncMock(return_value={'sensor.study_temperature':rows}))
        with patch('custom_components.dwellmind.coordinator.get_instance',return_value=recorder):
            await c.update_forecast(status);await c.update_forecast(status)
            self.assertEqual(recorder.async_add_executor_job.await_count,1)
            method,path,payload=c.client.request.call_args.args
            self.assertEqual((method,path),('POST','/v1/learning/forecast-temperature'))
            self.assertEqual(len(payload['rows']),27)
            self.assertEqual(payload['entity_id'],'sensor.study_temperature')
            c.forecast_hour=c.forecast_retry=None;c.scope_dirty=True
            await c.update_forecast(status)
            self.assertEqual(recorder.async_add_executor_job.await_count,1)
            c.scope_dirty=False;c.client.request.side_effect=WorkerError('Unavailable')
            await c.update_forecast(status)
            self.assertIsNone(c.forecast_hour)
            self.assertIsNotNone(c.forecast_retry)


class SchemaTests(unittest.TestCase):
    def test_reviewed_helper_retained_before_its_state_is_published_at_startup(self):
        from custom_components.dwellmind.discovery import candidates
        entry=SimpleNamespace(entity_id='sensor.study_average',area_id='study',device_id=None,
                              disabled_by=None,entity_category=None,device_class=None,original_device_class=None)
        areas=SimpleNamespace(async_get_area=lambda area:SimpleNamespace(name='Study'))
        devices=SimpleNamespace(devices={})
        entities=SimpleNamespace(entities={'example':entry})
        hass=SimpleNamespace(states=SimpleNamespace(get=lambda entity:None,async_all=lambda:[]))
        with patch('custom_components.dwellmind.discovery.ar.async_get',return_value=areas), \
             patch('custom_components.dwellmind.discovery.dr.async_get',return_value=devices), \
             patch('custom_components.dwellmind.discovery.er.async_get',return_value=entities):
            self.assertEqual(candidates(hass,['study']),{})
            self.assertEqual(rooms(hass,['study'],['sensor.study_average'],retain_review=True)[0]['entities'],['sensor.study_average'])
            self.assertEqual(rooms(hass,['study'],['sensor.study_average'],excluded_entities=['sensor.study_average'],retain_review=True),[])
    def test_real_ha_area_and_entity_picker_schemas(self):
        self.assertEqual(room_schema()({'areas':['office']}),{'areas':['office'],'excluded_areas':[],'excluded_entities':[]})
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
    async def test_unrelated_registry_update_preserves_queued_transitions_and_continuity(self):
        from collections import deque
        c=object.__new__(DwellMindCoordinator)
        c.selection=[{'entities':['light.study']}];c.entities=frozenset({'light.study'})
        c.reviewed_scope=lambda:c.selection
        c.scope_dirty=False;c.need_snapshot=False;c.queue=deque([{'before':'registry update'}])
        c.data={'state':'capturing'};c.client=SimpleNamespace(request=AsyncMock())
        c.invalidate_scope(None)
        c.enqueue({'after':'registry update'})
        await c.refresh_scope()
        self.assertFalse(c.scope_dirty)
        self.assertFalse(c.need_snapshot)
        self.assertEqual(list(c.queue),[{'before':'registry update'},{'after':'registry update'}])
        c.client.request.assert_not_awaited()

    async def test_registry_removal_discards_pending_transitions_before_delivery(self):
        from collections import deque
        c=object.__new__(DwellMindCoordinator)
        c.selection=[{'entities':['light.study']}];c.entities=frozenset({'light.study'})
        c.reviewed_scope=lambda:[];c.scope_dirty=False;c.need_snapshot=False
        c.queue=deque();c.data={'state':'capturing'};c.latest_summary=None
        c.client=SimpleNamespace(request=AsyncMock(side_effect=[{'state':'capturing'},{'state':'idle'},{'state':'idle'}]))
        c.async_set_updated_data=lambda _:None
        c.invalidate_scope(None);c.enqueue({'selected_entity':'light.study'})
        await c.refresh_scope()
        self.assertFalse(c.queue)
        self.assertTrue(c.need_snapshot)
        self.assertFalse(c.entities)
        self.assertEqual([call.args[1] for call in c.client.request.await_args_list],['/v1/status','/v1/stop','/v1/config'])

    async def test_bootstrap_does_not_touch_worker_before_home_assistant_is_running(self):
        from custom_components.dwellmind import async_setup_entry
        from homeassistant.exceptions import ConfigEntryNotReady
        with patch('custom_components.dwellmind.WorkerClient') as client:
            with self.assertRaises(ConfigEntryNotReady):
                await async_setup_entry(SimpleNamespace(is_running=False),SimpleNamespace())
            client.assert_not_called()
    async def test_all_excluded_scope_stops_capture_and_removes_worker_allowlist_without_history_deletion(self):
        coordinator=object.__new__(DwellMindCoordinator)
        coordinator.scope_dirty=True
        coordinator.selection=[{'entities':['light.study']}]
        coordinator.entities=frozenset({'light.study'})
        coordinator.reviewed_scope=lambda:[]
        coordinator.queue=[]
        coordinator.latest_summary={'rows':35}
        coordinator.need_snapshot=False
        coordinator.client=SimpleNamespace(request=AsyncMock(side_effect=[{'state':'capturing'},{'state':'idle'},{'state':'idle','room_count':0}]))
        coordinator.async_set_updated_data=lambda status:setattr(coordinator,'data',status)
        await coordinator.refresh_scope()
        self.assertEqual(coordinator.client.request.await_args_list[1].args,('POST','/v1/stop',{}))
        self.assertEqual(coordinator.client.request.await_args_list[2].args,('POST','/v1/config',{'rooms':[]}))
        self.assertTrue(coordinator.data['scope_paused'])
        self.assertFalse(coordinator.entities)
        self.assertTrue(coordinator.need_snapshot)
        from homeassistant.helpers.update_coordinator import UpdateFailed
        coordinator.entry=SimpleNamespace(options={'capture_seconds':300});coordinator.lock=asyncio.Lock()
        with self.assertRaises(UpdateFailed):await coordinator.start_capture(86400)

    async def test_excluded_model_is_preserved_but_not_exposed_as_usable(self):
        coordinator=object.__new__(DwellMindCoordinator)
        coordinator.selection=[];coordinator.entities=frozenset()
        result={'entity_id':'sensor.private','held_out_test':{'learned':{'mae':0.2}}}
        data=coordinator.scope_status({'learning_summary':result})
        self.assertIsNone(data['learning_summary'])
        self.assertEqual(result['entity_id'],'sensor.private')
        sensor=DwellMindSensor(SimpleNamespace(data=data),SimpleNamespace(entry_id='study'),'learning_summary','Learning','mdi:brain')
        self.assertEqual(sensor.native_value,'excluded')
        self.assertEqual(sensor.extra_state_attributes,{'historical_result_preserved':True,'model_use_blocked':True})
    async def test_explicit_long_capture_preserves_default_and_rejects_unbounded_duration(self):
        coordinator = object.__new__(DwellMindCoordinator)
        coordinator.entry = SimpleNamespace(options={'capture_seconds':300})
        coordinator.lock = asyncio.Lock()
        coordinator.selection = [{'area_id':'study','name':'Study','entities':['light.study']}]
        coordinator.latest_summary = None
        coordinator.client = SimpleNamespace(request=AsyncMock(return_value={'state':'capturing'}))
        coordinator.async_set_updated_data = lambda data: None
        coordinator.flush = AsyncMock()
        await coordinator.start_capture(86400)
        self.assertEqual(coordinator.client.request.await_args_list[-1].args[2], {'duration_seconds':86400})
        self.assertEqual(coordinator.entry.options['capture_seconds'], 300)
        from homeassistant.helpers.update_coordinator import UpdateFailed
        for value in [0, 86401, True]:
            with self.assertRaises(UpdateFailed):
                await coordinator.start_capture(value)

    async def test_training_sensor_and_actions_are_scoped_and_refuse_older_worker(self):
        handlers = {}
        coord = SimpleNamespace(entities={'sensor.study_temperature'}, data={'capabilities':[]})
        entry = SimpleNamespace(entry_id='study', runtime_data=coord)
        state = SimpleNamespace(attributes={'device_class':'temperature','unit_of_measurement':'°F'})
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda domain:[entry]),
            states=SimpleNamespace(get=lambda entity:state),
            services=SimpleNamespace(async_register=lambda domain, name, handler, **kwargs:handlers.update({name:handler})))
        register_services(hass)
        from homeassistant.exceptions import HomeAssistantError
        call = SimpleNamespace(data={'entity_id':'sensor.study_temperature','move_date':'2026-03-20','start_date':'2023-10-08'})
        with self.assertRaises(HomeAssistantError):
            await handlers['train_temperature_forecast'](call)
        sensor = DwellMindSensor(SimpleNamespace(data={'learning_summary':{'evaluation_status':'improves_baselines','unit':'°F'}}),
                                entry,'learning_summary','Temperature learning result','mdi:chart-bell-curve')
        self.assertEqual(sensor.native_value,'improves_baselines')
        self.assertEqual(sensor.extra_state_attributes['unit'],'°F')

    async def test_monthly_import_scopes_sensor_converts_seconds_and_requests_consistent_unit(self):
        from datetime import datetime, timezone
        handlers, calls = {}, []
        coord = SimpleNamespace(entities={'sensor.study_temperature'},
            data={'capabilities':['temperature_forecast_training']}, lock=asyncio.Lock(),
            client=SimpleNamespace(request=AsyncMock(return_value={'summary':{'evaluation_status':'improves_baselines'}})),
            async_set_updated_data=lambda data:None)
        entry = SimpleNamespace(entry_id='study',runtime_data=coord)
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda domain:[entry]),
            config=SimpleNamespace(time_zone='America/New_York'),
            states=SimpleNamespace(get=lambda entity:SimpleNamespace(attributes={'device_class':'temperature','unit_of_measurement':'°F'})),
            services=SimpleNamespace(async_register=lambda domain,name,handler,**kwargs:handlers.update({name:handler})))
        def query(hass_arg,start,end,entities,period,units,types):
            calls.append((start,end,entities,period,units,types))
            return {'sensor.study_temperature':[{'start':start.timestamp(),'mean':70.0}]}
        async def execute(fn):
            return fn()
        register_services(hass)
        call=SimpleNamespace(data={'entity_id':'sensor.study_temperature','move_date':'2026-03-20','start_date':'2026-01-01'})
        with patch('custom_components.dwellmind.services.get_instance',return_value=SimpleNamespace(async_add_executor_job=execute)), \
             patch('custom_components.dwellmind.services.statistics_during_period',side_effect=query):
            await handlers['train_temperature_forecast'](call)
        payload=coord.client.request.await_args.args[2]
        self.assertEqual(payload['rows'][0]['start'],int(datetime(2026,1,1,tzinfo=timezone.utc).timestamp()*1000))
        self.assertEqual(payload['move_date'],'2026-03-20T00:00:00-04:00')
        self.assertTrue(all(c[2:] == ({'sensor.study_temperature'},'hour',{'temperature':'°F'},{'mean'}) for c in calls))
        self.assertFalse(coord.training_active)
    async def test_summary_is_fetched_once_per_report_and_visible_to_ha_clients(self):
        coordinator=object.__new__(DwellMindCoordinator)
        coordinator.lock=asyncio.Lock()
        coordinator.report_reference=object()
        coordinator.latest_summary=None
        coordinator.selection=[{'entities':['light.study']}]
        coordinator.entities=frozenset({'light.study'})
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
        coordinator.selection=[{'entities':['light.study']}];coordinator.entities=frozenset({'light.study'})
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
