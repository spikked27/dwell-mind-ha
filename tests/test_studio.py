import http.client
import json
import threading
from types import SimpleNamespace
import unittest

from service_app import BoundedServer, Handler


class StudioHTTPTests(unittest.TestCase):
    def setUp(self):
        self.server=BoundedServer(('127.0.0.1',0),Handler)
        self.server.token='dummy-private-test-pairing-key-000000000000'
        self.server.worker=SimpleNamespace(context_view=lambda:{'protocol':1,'control_enabled':False,'scope':[],'observations':{}})
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)

    def request(self,path,headers=None,method='GET'):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        connection.request(method,path,headers=headers or {})
        response=connection.getresponse();raw=response.read();headers=dict(response.getheaders())
        connection.close()
        return response.status,headers,raw

    def test_public_shell_contains_no_household_data_and_sets_strict_browser_headers(self):
        status,headers,raw=self.request('/ui')
        self.assertEqual(status,200)
        self.assertIn(b'Evidence studio',raw)
        self.assertNotIn(self.server.token.encode(),raw)
        self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        self.assertEqual(headers['Referrer-Policy'],'no-referrer')
        for path in ['/ui/studio.js','/ui/studio.css','/ui/icon.svg']:
            self.assertEqual(self.request(path)[0],200)

    def test_context_requires_key_and_browser_requests_cannot_mutate_worker(self):
        self.assertEqual(self.request('/v1/context')[0],401)
        headers={'Authorization':'Bearer '+self.server.token}
        status,_,raw=self.request('/v1/context',headers)
        self.assertEqual(status,200)
        self.assertFalse(json.loads(raw)['control_enabled'])
        headers['Origin']='https://untrusted.example'
        self.assertEqual(self.request('/v1/context',headers)[0],403)
        self.assertEqual(self.request('/v1/config',headers,'POST')[0],403)
        headers.pop('Origin')
        for path in ['/ui/../service-token','/ui/index.html','/ui?key=private','/v1/context?path=/data/service-token']:
            self.assertEqual(self.request(path,headers)[0],404)

    def test_remembered_credential_only_reads_context_and_expires(self):
        from unittest.mock import patch
        headers={'Authorization':'Bearer '+self.server.token}
        status,_,raw=self.request('/v1/ui-session',headers)
        self.assertEqual(status,200)
        credential=json.loads(raw)['credential']
        self.assertNotIn(self.server.token,credential)
        readonly={'Authorization':'Bearer '+credential}
        self.assertEqual(self.request('/v1/context',readonly)[0],200)
        for path,method in [('/v1/config','POST'),('/v1/start','POST'),('/v1/learning/forecast-temperature','POST'),('/v1/ui-session','GET'),('/v1/status','GET')]:
            self.assertEqual(self.request(path,readonly,method)[0],401)
        expiry=int(credential.split('.')[1])
        with patch('service_app.time.time',return_value=expiry+1):
            self.assertEqual(self.request('/v1/context',readonly)[0],401)
        self.assertEqual(self.request('/v1/context',dict(readonly,Origin='http://evil.example'))[0],403)
        tampered=credential[:-1]+('0' if credential[-1]!='0' else '1')
        self.assertEqual(self.request('/v1/context',{'Authorization':'Bearer '+tampered})[0],401)
