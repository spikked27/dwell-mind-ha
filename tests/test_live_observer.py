import copy
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock

from ha_live_observer import Capture, LiveConfig, MAX_FRAME, guarded_frame_buffer, validate_command
from policy import SafeError
from private_journal import Journal
from rooms import OFFICE
from ws_states import StateDecoder

ROOT = Path(__file__).resolve().parents[1]


def config(**update):
    data = json.loads((ROOT / 'live-observer.example.json').read_text())
    data.update(rooms=['office'], duration_seconds=10, max_reconnects=0)
    data.update(update)
    return LiveConfig(data)


def snapshot(state='on', attrs=None):
    return {'id': 1, 'type': 'event', 'event': {'a': {OFFICE.primary_light:
            {'s':state, 'a':attrs or {}, 'lc':1790812800.123456,
             'c':{'id':'private-context', 'user_id':'private-user', 'parent_id':None}}}}}


class Clock:
    def __init__(self):
        self.value = 0
    def __call__(self):
        return self.value
    def sleep(self, value):
        self.value += value
    def wall(self):
        return 1790812800 * 10**9 + int(self.value * 10**9)


class FakeWS:
    def __init__(self, clock, messages, eof=False):
        self.clock, self.messages, self.eof = clock, list(messages), eof
        self.sent, self.closed = [], False
    def send(self, raw):
        self.sent.append(json.loads(raw))
    def recv(self):
        self.clock.value += 1
        if self.messages:
            return json.dumps(self.messages.pop(0))
        if self.eof:
            raise ConnectionError('dummy-sensitive-network-message')
        raise TimeoutError('idle')
    def close(self):
        self.closed = True


class MemoryJournal:
    def __init__(self):
        self.rows = []
    def write(self, row):
        self.rows.append(copy.deepcopy(row))


class DecoderTests(unittest.TestCase):
    def setUp(self):
        self.decoder = StateDecoder(OFFICE.entities)
        self.received = '2026-10-01T00:00:00Z'

    def test_snapshot_discards_titles_and_private_attributes(self):
        rows = self.decoder.decode(snapshot(attrs={'brightness':128,'media_title':'private-title','token':'dummy-secret'})['event'], self.received)
        self.assertEqual(rows[0]['record_kind'], 'snapshot')
        self.assertEqual(rows[0]['data']['new_state']['attributes'], {'brightness':128})
        self.assertNotIn('dummy-secret', str(self.decoder.states))
        self.assertNotIn('private-user', str(self.decoder.states))
        self.assertTrue(rows[0]['data']['new_state']['context']['user_id'])

    def test_partial_diff_preserves_state_and_context_but_removes_attributes(self):
        self.decoder.decode(snapshot(attrs={'brightness':100,'color_temp_kelvin':2700})['event'], self.received)
        diff = {'c':{OFFICE.primary_light:{'+':{'a':{'brightness':200},'c':'new-context','lu':1790812801},
                                         '-':{'a':['color_temp_kelvin']}}}}
        row = self.decoder.decode(diff, self.received)[0]
        new = row['data']['new_state']
        self.assertEqual(new['state'], 'on')
        self.assertEqual(new['attributes'], {'brightness':200})
        self.assertTrue(new['context']['user_id'])
        self.assertEqual(new['context']['id'], 'new-context')
        self.assertEqual(row['record_kind'], 'state_update')

    def test_context_delta_and_bad_attribute_do_not_reuse_stale_values(self):
        self.decoder.decode(snapshot(attrs={'brightness':100})['event'], self.received)
        diff = {'c':{OFFICE.primary_light:{'+':{'c':{'user_id':None,'parent_id':'parent'},'a':{'brightness':999999}}}}}
        new = self.decoder.decode(diff, self.received)[0]['data']['new_state']
        self.assertFalse(new['context']['user_id'])
        self.assertEqual(new['context']['id'], 'private-context')
        self.assertEqual(new['context']['parent_id'], 'parent')
        self.assertEqual(new['attributes'], {})

    def test_removal_and_reset_require_fresh_initial_state(self):
        self.decoder.decode(snapshot()['event'], self.received)
        row = self.decoder.decode({'r':[OFFICE.primary_light]}, self.received)[0]
        self.assertIsNone(row['data']['new_state'])
        with self.assertRaises(SafeError):
            self.decoder.decode({'c':{OFFICE.primary_light:{'+':{'s':'off'}}}}, self.received)
        self.decoder.reset()
        self.assertEqual(self.decoder.states, {})

    def test_scope_and_timestamp_bounds(self):
        bad = snapshot()['event']
        value = bad['a'].pop(OFFICE.primary_light)
        bad['a']['person.somebody'] = value
        with self.assertRaises(SafeError):
            self.decoder.decode(bad, self.received)
        bad = snapshot()['event']
        bad['a'][OFFICE.primary_light]['lc'] = float('inf')
        with self.assertRaises(SafeError):
            self.decoder.decode(bad, self.received)


class TransportTests(unittest.TestCase):
    def make(self, messages, **update):
        clock, journal = Clock(), MemoryJournal()
        ws = FakeWS(clock, messages)
        capture = Capture(config(**update), 'dummy-test-token-not-a-real-credential', journal,
                          connector=Mock(return_value=ws), clock=clock, sleep=clock.sleep, wall=clock.wall)
        return capture, ws, journal

    def test_scoped_commands_only_and_private_journal(self):
        capture, ws, journal = self.make([{'type':'auth_required'},{'type':'auth_ok'},
                                         {'type':'result','id':1,'success':True}, snapshot(attrs={'brightness':200,'password':'dummy-secret'})])
        stats = capture.run()
        self.assertFalse(stats['control_enabled'])
        self.assertTrue(ws.closed)
        subscriptions = [m for m in ws.sent if m['type']=='subscribe_entities']
        self.assertEqual(subscriptions[0]['entity_ids'], sorted(OFFICE.entities))
        self.assertNotIn('get_states', [m['type'] for m in ws.sent])
        self.assertNotIn('call_service', [m['type'] for m in ws.sent])
        serialized = json.dumps(journal.rows)
        for private in ['dummy-secret','private-user','private-context','dummy-test-token']:
            self.assertNotIn(private, serialized)
        observed = [r for r in journal.rows if r['record_kind']=='snapshot']
        self.assertEqual(len(observed), 1)
        self.assertFalse(observed[0]['eligible_preference_label'])
        self.assertIn('source_time', observed[0])
        self.assertTrue(all(r['state']=='unknown' for r in journal.rows[-len(OFFICE.entities):]))

    def test_optional_causal_denial_does_not_escalate(self):
        capture, ws, journal = self.make([{'type':'auth_required'},{'type':'auth_ok'},
                                         {'type':'result','id':2,'success':False,'error':{'message':'private-payload'}},
                                         {'type':'result','id':3,'success':False},
                                         {'type':'result','id':1,'success':True}, snapshot()])
        stats = capture.run()
        self.assertEqual(stats['attribution_subscriptions_denied'], 2)
        self.assertEqual(stats['connections'], 1)
        self.assertNotIn('private-payload', json.dumps(stats))
        self.assertTrue(any(r['record_kind']=='snapshot' for r in journal.rows))

    def test_entity_permission_denial_stops_without_retry(self):
        capture, ws, journal = self.make([{'type':'auth_required'},{'type':'auth_ok'},
                                         {'type':'result','id':1,'success':False}], max_reconnects=3)
        stats = capture.run()
        self.assertEqual(stats['stop_reason'], 'entity_permission_rejected')
        self.assertEqual(stats['connections'], 1)

    def test_auth_failure_stops_without_retry_or_echo(self):
        capture, ws, journal = self.make([{'type':'auth_required'},{'type':'auth_invalid','message':'dummy-test-token'}], max_reconnects=3)
        stats = capture.run()
        self.assertEqual(stats['stop_reason'], 'authentication_rejected')
        self.assertEqual(stats['connections'], 1)
        self.assertNotIn('dummy-test-token', json.dumps(stats))

    def test_reconnect_marks_gap_and_fresh_snapshots(self):
        clock, journal = Clock(), MemoryJournal()
        first = FakeWS(clock, [{'type':'auth_required'},{'type':'auth_ok'},snapshot('on')], eof=True)
        second = FakeWS(clock, [{'type':'auth_required'},{'type':'auth_ok'},snapshot('off')])
        capture = Capture(config(max_reconnects=1), 'dummy-token', journal, connector=Mock(side_effect=[first,second]),
                          clock=clock, sleep=clock.sleep, wall=clock.wall)
        stats = capture.run()
        self.assertEqual(stats['connections'], 2)
        observed = [(i,r) for i,r in enumerate(journal.rows) if r['record_kind']=='snapshot']
        self.assertEqual([r['state'] for i,r in observed], ['on','off'])
        self.assertTrue(any(r['record_kind']=='gap' for r in journal.rows[observed[0][0]+1:observed[1][0]]))
        self.assertTrue(first.closed and second.closed)

    def test_invalid_message_redacts_error(self):
        capture, ws, journal = self.make([{'type':'auth_required'},{'type':'auth_ok'}, {'type':'arbitrary','secret':'private-value'}])
        stats = capture.run()
        self.assertEqual(stats['connection_failures'], 1)
        self.assertNotIn('private-value', json.dumps(stats))

    def test_heartbeat_success_and_missing_reply(self):
        capture, ws, journal = self.make([{'type':'auth_required'},{'type':'auth_ok'},snapshot()], duration_seconds=40)
        original = ws.send
        def reply_to_ping(raw):
            original(raw)
            message = json.loads(raw)
            if message['type'] == 'ping':
                ws.messages.append({'id':message['id'],'type':'pong'})
        ws.send = reply_to_ping
        stats = capture.run()
        self.assertEqual(stats['connection_failures'],0)
        self.assertTrue(any(m['type']=='ping' for m in ws.sent))
        capture, ws, journal = self.make([{'type':'auth_required'},{'type':'auth_ok'},snapshot()], duration_seconds=60)
        stats = capture.run()
        self.assertEqual(stats['connection_failures'],1)
        self.assertLess(capture.clock(),40)

    def test_receive_budget_stops_stream(self):
        capture, ws, journal = self.make([{'type':'auth_required'},{'type':'auth_ok'}]
                                        + [{'id':1,'type':'result','success':True}]*30,
                                        max_messages=20, duration_seconds=60)
        stats = capture.run()
        self.assertEqual(stats['stop_reason'],'message_budget')
        self.assertLessEqual(stats['receive_attempts'],21)

    def test_mutation_and_all_home_commands_refused(self):
        for command in [{'id':1,'type':'call_service','domain':'light'}, {'id':1,'type':'fire_event'},
                        {'id':1,'type':'get_states'}, {'id':1,'type':'subscribe_entities','entity_ids':[]},
                        {'id':1,'type':'subscribe_events','event_type':'*'}]:
            with self.assertRaises(SafeError):
                validate_command(command, OFFICE.entities)

    def test_live_config_security(self):
        for update in [{'ha_url':'http://localhost:8123'}, {'ha_url':'https://u:p@host'},
                       {'ha_url':'https://host/path'}, {'ha_url':'https://host?access_token=x'},
                       {'rooms':['bedroom']}, {'max_reconnects':11}, {'duration_seconds':True},
                       {'ha_token_file':'relative-secret'}, {'journal_directory':'relative'}]:
            with self.assertRaises(SafeError):
                config(**update)
        self.assertEqual(config(ha_url='http://localhost:8123',allow_plaintext_ha=True).url, 'ws://localhost:8123/api/websocket')

    def test_frame_guard_checks_before_payload(self):
        class Base:
            def recv_length(self):
                self.length = self.test_length
        guard = guarded_frame_buffer(Base)()
        guard.header = (1,0,0,0,1,0,0)
        guard.test_length = MAX_FRAME
        guard.recv_length()
        for length, fin, opcode in [(MAX_FRAME+1,1,1),(10,0,1),(10,1,0)]:
            guard.test_length, guard.header = length, (fin,0,0,0,opcode,0,0)
            with self.assertRaises(SafeError):
                guard.recv_length()


class JournalTests(unittest.TestCase):
    def directory(self):
        return tempfile.TemporaryDirectory(prefix='observer-mock-', dir=__import__('os').environ.get('DWELLMIND_TEST_TMPDIR'))

    def test_rotation_ownership_and_disk_budget(self):
        with self.directory() as directory:
            journal = Journal(directory, 'test', max_file_bytes=1024, max_files=2)
            try:
                journal.write({'value':'x'*900})
                journal.write({'value':'x'*900})
                with self.assertRaises(SafeError):
                    journal.write({'value':'x'*900})
            finally:
                journal.close()
            files = list(Path(directory).glob('*.jsonl'))
            self.assertEqual(len(files),2)
            self.assertTrue(all(stat.S_IMODE(p.stat().st_mode)==0o600 for p in files))
            self.assertTrue(all(p.stat().st_size<=1024 for p in files))

    def test_existing_file_never_overwritten(self):
        with self.directory() as directory:
            existing = Path(directory)/'test-01.jsonl'
            existing.write_text('user-content')
            journal = Journal(directory, 'test')
            try:
                with self.assertRaises(FileExistsError):
                    journal.write({'new':True})
            finally:
                journal.close()
            self.assertEqual(existing.read_text(),'user-content')

    def test_unprotected_or_symlink_directory_refused(self):
        with self.directory() as directory:
            child = Path(directory)/'unsafe'
            child.mkdir(mode=0o755)
            child.chmod(0o755)
            with self.assertRaises(SafeError):
                Journal(child, 'test')
            link = Path(directory)/'link'
            link.symlink_to(child)
            with self.assertRaises(OSError):
                Journal(link, 'test')
