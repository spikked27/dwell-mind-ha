"""Real pinned WebSocket library against a local dummy HA server, no real HA."""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import struct
import threading
import unittest

from ha_live_observer import MAX_FRAME, connect
from policy import SafeError
from test_live_observer import config


class DummyHA(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'

    def log_message(self,*args):
        pass

    def send_text(self,obj):
        data=json.dumps(obj).encode()
        header=bytes([0x81,len(data)]) if len(data)<126 else bytes([0x81,126])+struct.pack('!H',len(data))
        self.wfile.write(header+data)
        self.wfile.flush()

    def recv_json(self):
        header=self.rfile.read(2)
        if len(header)!=2:
            raise ValueError('Incomplete client frame')
        size=header[1]&127
        if size==126:
            size=struct.unpack('!H',self.rfile.read(2))[0]
        if size>4096 or not header[1]&128:
            raise ValueError('Invalid test client frame')
        mask=self.rfile.read(4)
        data=self.rfile.read(size)
        return json.loads(bytes(byte^mask[i%4] for i,byte in enumerate(data)))

    def do_GET(self):
        self.connection.settimeout(3)
        if self.path!='/api/websocket':
            self.send_error(404)
            return
        key=self.headers.get('Sec-WebSocket-Key','')
        accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.send_response(101)
        self.send_header('Upgrade','websocket')
        self.send_header('Connection','Upgrade')
        self.send_header('Sec-WebSocket-Accept',accept)
        self.end_headers()
        try:
            if self.server.mode=='oversized':
                # No payload sent: the client must reject the declared size first.
                self.wfile.write(b'\x81\x7f'+struct.pack('!Q',MAX_FRAME+1))
                self.wfile.flush()
            elif self.server.mode=='fragmented':
                self.wfile.write(b'\x01\x00')
                self.wfile.flush()
            else:
                self.send_text({'type':'auth_required','ha_version':'dummy'})
                auth=self.recv_json()
                self.server.received.append({'type':auth.get('type')})  # Never retain token.
                self.send_text({'type':'auth_ok'})
                subscription=self.recv_json()
                self.server.received.append(subscription)
                self.send_text({'id':subscription['id'],'type':'result','success':True})
                entities={entity:{'s':'off','a':{},'lc':1790812800,'c':{}} for entity in subscription['entity_ids']}
                self.send_text({'id':subscription['id'],'type':'event','event':{'a':entities}})
        except (OSError,ValueError):
            self.server.failed=True
        self.close_connection=True


@unittest.skipUnless(importlib.util.find_spec('websocket'),'optional pinned dependency not installed')
class WebSocketIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.server=ThreadingHTTPServer(('127.0.0.1',0),DummyHA)
        self.server.mode='normal'
        self.server.received=[]
        self.server.failed=False
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def client(self):
        port=self.server.server_address[1]
        return connect(config(ha_url=f'http://127.0.0.1:{port}',allow_plaintext_ha=True))

    def test_actual_library_auth_and_atomic_scoped_snapshot(self):
        ws=self.client()
        try:
            self.assertEqual(json.loads(ws.recv())['type'],'auth_required')
            ws.send(json.dumps({'type':'auth','access_token':'dummy-local-token'}))
            self.assertEqual(json.loads(ws.recv())['type'],'auth_ok')
            ws.send(json.dumps({'id':1,'type':'subscribe_entities','entity_ids':['light.example']}))
            self.assertTrue(json.loads(ws.recv())['success'])
            self.assertEqual(set(json.loads(ws.recv())['event']['a']),{'light.example'})
        finally:
            ws.close()
        self.assertFalse(self.server.failed)
        self.assertEqual(self.server.received[1]['entity_ids'],['light.example'])

    def test_actual_library_rejects_oversized_before_payload(self):
        self.server.mode='oversized'
        ws=self.client()
        try:
            with self.assertRaises(SafeError):
                ws.recv()
        finally:
            ws.close()

    def test_actual_library_rejects_fragmentation(self):
        self.server.mode='fragmented'
        ws=self.client()
        try:
            with self.assertRaises(SafeError):
                ws.recv()
        finally:
            ws.close()
