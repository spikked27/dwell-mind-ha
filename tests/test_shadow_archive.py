from collections import deque
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from container_app import write_json
from policy import SafeError, instant
from office_collect import rfc3339_ns
from shadow_archive import Archive, covered_until, page_end, source_identity, failure_code
from shadow_campaign import read_document


class RangeTests(unittest.TestCase):
    def test_overlaps_do_not_skip_unqueried_holes(self):
        self.assertEqual(covered_until(0,100,[(0,10),(5,20),(30,90)]),20)
        self.assertEqual(covered_until(20,100,[(0,20),(30,90)]),20)
        self.assertEqual(covered_until(30,50,[(30,90)]),50)

    def test_full_page_only_covers_through_last_timestamp_not_query_end(self):
        begin=instant('2026-04-01T00:00:00Z');end=begin+86400*10**9
        page={'start':rfc3339_ns(begin),'end':rfc3339_ns(end),
              'records':[{'time':rfc3339_ns(begin+i*10**9)} for i in range(501)]}
        self.assertEqual(page_end(page),begin+500*10**9+1)
        page['records']=[];self.assertEqual(page_end(page),end)
        page['records']=[{'time':rfc3339_ns(begin)}]*2
        with self.assertRaises(SafeError):page_end(page)

    def test_reason_codes_cannot_echo_arbitrary_upstream_errors(self):
        self.assertEqual(failure_code(RuntimeError('dummy-secret')),'unexpected_import_error')
        self.assertEqual(failure_code(SafeError('Upstream authentication failed.')),'upstream_authentication_failed')
        self.assertNotEqual(source_identity('http://archive.example:8086','a'),source_identity('http://archive.example:8086','b'))


class ImmediateThread:
    def __init__(self,target,**_):self.target=target
    def start(self):self.target()


@unittest.skipUnless(hasattr(os,'geteuid'),'Private archive documents require POSIX ownership')
class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        directory=self.root/'shadow';folder=directory/'campaign-test';folder.mkdir(parents=True,mode=0o700)
        directory.chmod(0o700)
        self.allowed={'light.study'}
        self.c=SimpleNamespace(directory=directory,folder=folder,state='running',campaign_id='campaign-test',
            lock=threading.RLock(),samples=deque(),policy={'move_date':'2026-03-20T04:00:00Z'},
            last_training=0,retrain=lambda allowed:None)
        self.observations={'light.study':{'room':'Study','availability':'reported','unit':None,'device_class':None}}
        self.end=instant('2026-04-30T00:00:00Z')
        self.payload={'url':'http://'+str(__import__('ipaddress').ip_address(0xC0A8006B))+':8086',
            'database':'homeassistant','username':'dummy-user','password':'dummy-secret','start':'2026-04-01T00:00:00Z'}

    def tearDown(self):self.temp.cleanup()

    def start(self,archive,client,payload=None):
        with patch('shadow_archive.InfluxClient',return_value=client),patch('shadow_archive.time.time_ns',return_value=self.end),\
             patch('shadow_archive.threading.Thread',ImmediateThread),patch('shadow_archive.time.sleep'):
            return archive.start(payload or self.payload,self.observations)

    def test_partial_restart_resume_preserves_pages_and_never_requeries_confirmed_range(self):
        begin=self.end-21*86400*10**9;queries=[]
        records=[[rfc3339_ns(begin+i*10**9),'on'] for i in range(501)]
        def query(q):
            queries.append(q)
            if len(queries)>1:raise SafeError('Upstream connection failed.')
            return {'series':[{'columns':['time','state'],'values':records}]}
        a=Archive(self.c,lambda:self.allowed);self.start(a,SimpleNamespace(query=query))
        self.assertEqual(a.status['state'],'partial')
        self.assertEqual(a.status['error_code'],'upstream_connection_error')
        page=next(self.c.directory.glob('campaign-*/archive-*/source-*.json'));before=page.read_bytes()
        restored=Archive(self.c,lambda:self.allowed)
        self.assertEqual(restored.status['error_code'],'upstream_connection_error')
        requests=[]
        self.start(restored,SimpleNamespace(query=lambda q:(requests.append(q) or {})))
        self.assertEqual(restored.status['state'],'completed')
        self.assertEqual(restored.status['reused_source_rows'],501)
        self.assertIn(rfc3339_ns(begin+500*10**9+1),requests[0])
        self.assertEqual(page.read_bytes(),before)
        self.assertNotIn('dummy-secret',''.join(p.read_text() for p in self.c.directory.glob('campaign-*/archive-*/*.json')))
        third=Archive(self.c,lambda:self.allowed);self.start(third,SimpleNamespace(query=lambda _:self.fail('Completed ranges queried again')))
        self.assertEqual(third.status['state'],'completed')

    def test_legacy_reuse_requires_confirmation_and_matching_metadata(self):
        folder=self.c.folder/'archive-old';folder.mkdir(mode=0o700)
        source={'entity_id':'light.study','measurement':'state','metadata':{'room':'Study','unit':None,'device_class':None},
            'start':'2026-04-01T00:00:00Z','end':'2026-04-30T00:00:00Z','records':[]}
        write_json(folder/'source-old.json',source)
        a=Archive(self.c,lambda:self.allowed);identity=source_identity(self.payload['url'],self.payload['database'])
        self.assertFalse(a.saved_ranges(identity,self.observations,False)[0])
        self.assertTrue(a.saved_ranges(identity,self.observations,True)[0])
        self.assertFalse(a.saved_ranges(identity,{'light.study':{'room':'Other'}},True)[0])

    def test_restart_marks_inflight_job_interrupted_without_automatic_credentials(self):
        folder=self.c.folder/'archive-old';folder.mkdir(mode=0o700)
        write_json(folder/'status.json',{'state':'importing','updated_ns':1,'queries':12})
        a=Archive(self.c,lambda:self.allowed)
        self.assertEqual(a.status['state'],'interrupted')
        self.assertTrue(a.status['credentials_required'])
        self.assertFalse(a.running)

    def test_training_failure_does_not_mark_download_incomplete(self):
        a=Archive(self.c,lambda:self.allowed)
        with patch('shadow_archive.snapshots',side_effect=RuntimeError('dummy-secret')):
            self.start(a,SimpleNamespace(query=lambda _:{}))
        self.assertEqual(a.status['state'],'completed')
        self.assertIsNotNone(a.status['training_error_code'])
        self.assertEqual(Archive(self.c,lambda:self.allowed).status['state'],'completed')
