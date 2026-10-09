"""Real aiohttp client against the actual worker HTTP handler, with dummy keys."""
import importlib.util
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest

import aiohttp

from service_app import BoundedServer, Handler

spec=importlib.util.spec_from_file_location('dwellmind_client',Path(__file__).resolve().parents[1]/'custom_components/dwellmind/api.py')
api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server=BoundedServer(('127.0.0.1',0),Handler)
        self.server.token='dummy-key-only-for-local-test-00000000000'
        self.server.worker=SimpleNamespace(status=lambda:{'protocol':1,'control_enabled':False,'state':'idle'})
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
        self.session=aiohttp.ClientSession()

    async def asyncTearDown(self):
        await self.session.close()
        self.server.shutdown();self.server.server_close();self.thread.join(2)

    async def test_real_response_and_safe_paste_whitespace(self):
        client=api.WorkerClient(self.session,' '+self.url+' ', '\n'+self.server.token+'\n',True)
        result=await client.request('GET','/v1/status')
        self.assertEqual(result['state'],'idle')

    async def test_authentication_is_distinct_from_network_and_protocol(self):
        wrong=api.WorkerClient(self.session,self.url,'dummy-wrong-key-000000000000000000000000',True)
        with self.assertRaises(api.WorkerAuthError):await wrong.request('GET','/v1/status')
        self.server.worker=SimpleNamespace(status=lambda:{'healthy':True})
        client=api.WorkerClient(self.session,self.url,self.server.token,True)
        with self.assertRaises(api.WorkerError) as caught:await client.request('GET','/v1/status')
        self.assertEqual(caught.exception.code,'incompatible_worker')

    async def test_plaintext_consent_and_url_errors_do_not_echo_inputs(self):
        with self.assertRaises(api.WorkerError) as caught:api.WorkerClient(self.session,self.url,self.server.token)
        self.assertEqual(caught.exception.code,'http_not_allowed')
        with self.assertRaises(api.WorkerError) as caught:api.WorkerClient(self.session,'worker.example:8128',self.server.token,True)
        self.assertEqual(caught.exception.code,'invalid_url')
        self.assertNotIn(self.server.token,str(caught.exception))

    async def test_report_summary_retrieval_uses_existing_pairing_without_key_in_response(self):
        self.server.worker.latest_report=lambda:{'protocol':1,'control_enabled':False,'summary':{'rows':35,'duration_seconds':300}}
        client=api.WorkerClient(self.session,self.url,self.server.token,True)
        response=await client.request('GET','/v1/report/latest')
        self.assertEqual(response['summary']['rows'],35)
        self.assertNotIn(self.server.token,str(response))

    async def test_historical_upload_has_explicit_bounded_endpoint(self):
        observed=[]
        self.server.worker.train_temperature=lambda payload: (observed.append(payload) or {'protocol':1,'control_enabled':False,'summary':{'rows':5000}})
        client=api.WorkerClient(self.session,self.url,self.server.token,True)
        payload={'rows':[{'start':i*3600000,'mean':70} for i in range(5000)]}
        response=await client.request('POST','/v1/learning/train-temperature',payload)
        self.assertEqual(response['summary']['rows'],5000)
        self.assertEqual(len(observed[0]['rows']),5000)
        with self.assertRaises(api.WorkerError):
            await client.request('POST','/v1/events',payload)

    async def test_native_campaign_and_forecast_reach_authenticated_worker(self):
        observed=[]
        self.server.worker.profiles=[SimpleNamespace(entities={'light.test'})]
        self.server.worker.shadow=SimpleNamespace(
            start=lambda payload,allowed: (observed.append(('start',payload,allowed)) or {'state':'running'}),
            stop=lambda: observed.append(('stop',)))
        self.server.worker.forecast_temperature=lambda payload: (
            observed.append(('forecast',payload)) or {'protocol':1,'control_enabled':False})
        client=api.WorkerClient(self.session,self.url,self.server.token,True)
        payload={'days':14,'move_date':'2026-03-20T00:00:00-04:00','timezone':'America/New_York'}
        result=await client.request('POST','/v1/shadow/start',payload)
        self.assertEqual(result['shadow']['state'],'running')
        self.assertEqual(observed[0],('start',payload,{'light.test'}))
        await client.request('POST','/v1/shadow/stop',{})
        await client.request('POST','/v1/learning/forecast-temperature',{'rows':[]})
        self.assertEqual(observed[1:], [('stop',),('forecast',{'rows':[]})])
        for path in ('/v1/shadow/archive','/v1/shadow/feedback','/api/services/light/turn_on'):
            with self.assertRaises(api.WorkerError):
                await client.request('POST',path,{})
