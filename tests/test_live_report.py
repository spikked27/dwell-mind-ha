import copy
import unittest

from live_report import summarize
from policy import SafeError
from rooms import OFFICE


def row(minute, kind, state='unknown', actor='unattributed'):
    return {'time':f'2026-10-06T12:{minute:02d}:00Z','room':'Office','entity_id':OFFICE.primary_light,
            'record_kind':kind,'state':state,'availability':'reported' if state in {'on','off'} else 'unknown',
            'actor':actor,'eligible_preference_label':False}


class LiveReportTests(unittest.TestCase):
    def test_snapshots_not_actions_and_gaps_not_absence(self):
        rows = [row(0,'gap'),row(1,'snapshot','on','user_associated'),row(2,'state_update','off','engine'),row(3,'gap')]
        report = summarize(rows,[OFFICE])
        light = report['entities'][OFFICE.primary_light]
        self.assertEqual(light['light_update_actor_counts'],{'engine':1})
        self.assertEqual(light['reported_state_minutes'],2)
        self.assertEqual(light['state_minutes']['unknown'],1)
        self.assertEqual(report['entities'][OFFICE.pir]['state_minutes']['unknown'],3)
        self.assertEqual(report['preference_labels'],0)
        self.assertFalse(report['control_enabled'])

    def test_eof_not_extended(self):
        report = summarize([row(0,'gap'),row(1,'snapshot','on'),row(2,'state_update','on')],[OFFICE])
        self.assertFalse(report['all_entities_have_terminal_gap'])
        self.assertEqual(report['entities'][OFFICE.primary_light]['reported_state_minutes'],1)

    def test_wrong_room_labels_or_order_fail_closed(self):
        for altered in [dict(row(0,'gap'),room='Living Room'),dict(row(0,'gap'),eligible_preference_label=True),
                        dict(row(0,'gap'),entity_id='person.somebody')]:
            with self.assertRaises(SafeError):
                summarize([altered],[OFFICE])
        with self.assertRaises(SafeError):
            summarize([row(2,'gap'),row(1,'snapshot','on')],[OFFICE])
