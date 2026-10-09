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
