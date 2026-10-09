import copy
import http.client
import ipaddress
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from policy import SafeError
from service_app import BoundedServer, Handler, Worker, private_peer, selections


ROOMS = [{'area_id':'study','name':'Study','entities':['light.study','sensor.study_temperature']}]


def event(entity='light.study', state='on', ctx=None):
    ctx = ctx or {}
    result = {'event_type':'state_changed','context':ctx,'data':{'entity_id':entity,
              'new_state':{'state':state,'attributes':{},'context':ctx}}}
    if entity == 'sensor.study_temperature':
        result['data']['new_state'].update(device_class='temperature',unit='°C')
    return result


class Clock:
    def __init__(self): self.value = 1
    def __call__(self): return self.value


class SelectionTests(unittest.TestCase):
    def test_listener_accepts_private_lan_and_loopback_but_not_public_sources(self):
        self.assertTrue(private_peer('127.0.0.1'))
        self.assertTrue(private_peer(str(ipaddress.ip_address(0xC0A80102))))
        self.assertTrue(private_peer('fd00::2'))
        self.assertFalse(private_peer('203.0.113.8'))
        self.assertFalse(private_peer('not-an-address'))
    def test_multiple_entities_without_guessed_sensor_roles(self):
        self.assertEqual(list(selections(ROOMS)[0].entities),ROOMS[0]['entities'])

    def test_duplicate_cross_room_out_of_scope_and_oversize_rejected(self):
        for value in [[], ROOMS*2, [{'area_id':'x','name':'X','entities':['switch.private']}],
                      [{'area_id':'x','name':'X','entities':[f'light.e{i}' for i in range(41)]}],
                      [{'area_id':'x','name':'X','entities':[{}]}]]:
            with self.assertRaises(SafeError): selections(value)


@unittest.skipUnless(hasattr(os,'geteuid'),'Worker journals require POSIX permissions')
class WorkerTests(unittest.TestCase):
    def test_live_context_is_allowlisted_and_scope_change_removes_private_active_inputs(self):
        self.worker.ingest(self.batch())
        view=self.worker.context_view()
        self.assertEqual(view['scope'],ROOMS)
        self.assertEqual(view['observations']['sensor.study_temperature']['value'],-5.0)
        raw=json.dumps(view)
        self.assertNotIn('user_id',raw)
        self.assertNotIn('attributes',raw)
        self.assertFalse(view['context_activity_models_available'])
        self.assertFalse(view['human_hypotheses_applied_to_models'])
        self.worker.stop();self.worker.configure([])
        self.assertEqual(self.worker.context_view()['observations'],{})

    def test_empty_scope_pauses_without_erasing_captures_or_models(self):
        self.worker.ingest(self.batch());self.worker.stop()
        report=self.worker.last_report
        status=self.worker.configure([])
        self.assertEqual(status['entity_count'],0)
        self.assertTrue((self.worker.directory/'reports'/(report+'.json')).exists())
        with self.assertRaises(SafeError):self.worker.start(300)
        from test_thermal_forecast import dataset
        source=dataset();source['entity_id']='sensor.study_temperature'
        with self.assertRaises(SafeError):self.worker.train_temperature(source)

    def test_training_preserves_history_and_restores_summary_after_restart(self):
        from test_thermal_forecast import dataset
        import hashlib
        source = dataset()
        source['entity_id'] = 'sensor.study_temperature'
        prior = self.worker.folder/'selection.json'
        digest = hashlib.sha256(prior.read_bytes()).hexdigest()
        response = self.worker.train_temperature(source)
        self.assertFalse(response['control_enabled'])
        self.assertNotIn('model',response['summary'])
        self.assertEqual(hashlib.sha256(prior.read_bytes()).hexdigest(),digest)
        self.assertEqual(len(list((self.worker.directory/'learning').glob('history-*.json'))),1)
        restored = Worker(self.temp.name)
        self.assertEqual(restored.learning_summary,response['summary'])
        self.assertFalse(restored.learning_error)
        source['entity_id'] = 'sensor.not_selected'
        with self.assertRaises(SafeError):
            self.worker.train_temperature(source)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.worker = Worker(self.temp.name,clock=self.clock)
        self.worker.configure(copy.deepcopy(ROOMS))
        self.worker.start(300)
        self.addCleanup(lambda:self.worker.stop())

    def batch(self, sequence=1, snapshot=True, events=None):
        return {'capture_id':self.worker.run_id,'sequence':sequence,'snapshot':snapshot,
                'events':events if events is not None else [event(),event('sensor.study_temperature','-5')]}

    def test_capture_terminal_gap_negative_temperature_and_private_history(self):
        self.worker.ingest(self.batch())
        self.worker.ingest(self.batch(2,False,[event(state='off',ctx={'user_id':True})]))
        self.worker.stop()
        report=json.loads(next((Path(self.temp.name)/'reports').glob('*.json')).read_text())
        self.assertEqual(report['preference_labels'],0)
        self.assertTrue(report['all_entities_have_terminal_gap'])
        self.assertEqual(report['entities']['light.study']['light_update_actor_counts'],{'user_associated':1})
        raw=''.join(p.read_text() for p in (self.worker.folder).glob('*.jsonl'))
        self.assertIn('"value":-5.0',raw)
        self.assertNotIn('user_id',raw)
        self.assertEqual((self.worker.folder/'selection.json').stat().st_mode & 0o777,0o600)

    def test_missing_heartbeat_and_reconnect_snapshot_are_unknown(self):
        self.worker.ingest(self.batch())
        self.clock.value += 31
        self.worker.tick()
        with self.assertRaises(SafeError): self.worker.ingest(self.batch(2,False,[]))
        self.worker.ingest(self.batch(3,True))
        self.assertEqual(self.worker.gaps,2)

    def test_batches_are_idempotent_but_not_replayed_as_actions(self):
        batch=self.batch()
        self.worker.ingest(batch)
        rows=self.worker.rows
        self.worker.ingest(batch)
        self.assertEqual(self.worker.rows,rows)
        with self.assertRaises(SafeError): self.worker.ingest(self.batch(events=[event(state='off'),event('sensor.study_temperature','3')]))

    def test_sequence_gap_requires_snapshot(self):
        self.worker.ingest(self.batch())
        with self.assertRaises(SafeError): self.worker.ingest(self.batch(3,False,[]))
        self.assertTrue(self.worker.await_snapshot)
        self.worker.ingest(self.batch(4,True))

    def test_complete_snapshot_and_allowlist_enforced_before_any_write(self):
        rows=self.worker.rows
        for events in [[event()],[event(),event('sensor.unselected')],[event(),event()],
                       [event(),{'event_type':'call_service','data':{},'context':{}}]]:
            with self.assertRaises(SafeError): self.worker.ingest(self.batch(events=events))
            self.assertEqual(self.worker.rows,rows)

    def test_causal_events_never_preference_labels_or_private_context(self):
        self.worker.ingest(self.batch())
        cause={'event_type':'automation_triggered','context':{'id':'private-context'},'data':{'entity_id':'automation.study'}}
        update=event(ctx={'parent_id':'private-context'})
        self.worker.ingest(self.batch(2,False,[cause,update]))
        self.worker.stop()
        report=json.loads(next((Path(self.temp.name)/'reports').glob('*.json')).read_text())
        self.assertEqual(report['entities']['light.study']['light_update_actor_counts'],{'automation':1})
        self.assertEqual(report['preference_labels'],0)
        self.assertNotIn('private-context',''.join(p.read_text() for p in self.worker.folder.glob('*.jsonl')))

    def test_selection_changes_refused_during_capture_but_reconnect_allowed(self):
        self.worker.configure(copy.deepcopy(ROOMS))
        altered=copy.deepcopy(ROOMS);altered[0]['entities']=['light.study']
        with self.assertRaises(SafeError):self.worker.configure(altered)

    def test_deadline_stops_without_ingest_or_poll(self):
        self.clock.value+=300
        self.worker.tick()
        self.assertIsNone(self.worker.journal)
        self.assertEqual(self.worker.state,'idle')

    def test_summary_restored_after_worker_upgrade_without_erasing_history(self):
        self.worker.ingest(self.batch());self.worker.stop()
        report_id=self.worker.last_report
        restored=Worker(self.temp.name)
        self.assertEqual(restored.last_report,report_id)
        summary=restored.latest_report()['summary']
        self.assertEqual(summary['entity_count'],2)
        self.assertTrue(summary['all_entities_have_terminal_gap'])
        self.assertEqual(len(list((Path(self.temp.name)/'captures').iterdir())),1)

    def test_history_is_not_deleted_at_retention_cap(self):
        self.worker.stop()
        for i in range(63):(Path(self.temp.name)/'captures'/f'old-{i}').mkdir()
        with self.assertRaises(SafeError):self.worker.start()
        self.assertEqual(len(list((Path(self.temp.name)/'captures').iterdir())),64)

    def test_disk_error_closes_job_without_unbounded_retry(self):
        with patch.object(self.worker.journal,'write',side_effect=SafeError('Disk budget')):
            with self.assertRaises(SafeError):self.worker.ingest(self.batch())
        self.assertIsNone(self.worker.journal)
        self.assertEqual(self.worker.state,'storage_error')


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.worker = type('Dummy',(),{'status':lambda _: {'protocol':1,'control_enabled':False}})()
        self.server=BoundedServer(('127.0.0.1',0),Handler)
        self.server.worker=self.worker
        self.server.token='dummy-worker-key-not-a-real-secret-000000'
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)

    def request(self,path,method='GET',headers=None,body=None):
        client=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=2)
        client.request(method,path,headers=headers or {},body=body)
        response=client.getresponse();status=response.status;data=response.read();client.close()
        return status,data

    def test_health_has_no_private_status_and_all_api_requires_key(self):
        self.assertEqual(json.loads(self.request('/health')[1]),{'healthy':True})
        self.assertEqual(self.request('/v1/status')[0],401)
        code,raw=self.request('/v1/status',headers={'Authorization':'Bearer '+self.server.token})
        self.assertEqual(code,200)
        self.assertNotIn(self.server.token.encode(),raw)
        self.assertEqual(self.request('/v1/report/latest')[0],401)

    def test_report_route_is_read_only_and_not_an_arbitrary_file_reader(self):
        self.worker.latest_report=lambda:{'protocol':1,'control_enabled':False,'summary':{'rows':35}}
        headers={'Authorization':'Bearer '+self.server.token}
        self.assertEqual(self.request('/v1/report/latest',headers=headers)[0],200)
        self.assertEqual(self.request('/v1/report/../../service-token',headers=headers)[0],404)

    def test_browser_origin_and_unknown_mutating_commands_refused(self):
        self.assertEqual(self.request('/health',headers={'Origin':'http://untrusted.example'})[0],403)
        self.assertEqual(self.request('/v1/call_service','POST',headers={'Authorization':'Bearer '+self.server.token})[0],404)

    def test_public_peer_rejected_even_with_valid_key(self):
        with patch('service_app.private_peer',return_value=False):
            self.assertEqual(self.request('/v1/status',headers={'Authorization':'Bearer '+self.server.token})[0],403)

    def test_chunked_and_oversize_bodies_refused(self):
        headers={'Authorization':'Bearer '+self.server.token,'Content-Type':'application/json','Content-Length':'999999'}
        self.assertEqual(self.request('/v1/start','POST',headers=headers,body='{}')[0],400)
