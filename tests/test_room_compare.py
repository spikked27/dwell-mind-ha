import copy
import unittest

from room_compare import compare, fraction, markdown


def fixture(room):
    return {'room': room, 'start': '2026-09-28T00:00:00-04:00', 'end': '2026-10-05T00:00:00-04:00',
            'timezone': 'America/New_York', 'totals': {'duration_minutes': 10080, 'presence_evidence_known_minutes': 1008,
                                                     'both_sensors_known_minutes': 100, 'sensor_disagreement_minutes': 10},
            'collection': {'rows': 0, 'queries': 0, 'entities': {}},
            'routine_baseline': {'evaluation': {'test_bins': 10, 'hourly_brier': 0.2, 'constant_brier': 0.1}},
            'review_candidates': []}


class ComparisonTests(unittest.TestCase):
    def test_percentages_and_no_control(self):
        result = compare([fixture('Office'), fixture('Living Room')])
        for room in result['rooms']:
            self.assertEqual(room['inferred_known_coverage_percent'], 10)
            self.assertEqual(room['sensor_disagreement_percent_of_both_known'], 10)
            self.assertFalse(room['control_enabled'])
            self.assertFalse(room['hourly_better_in_this_sample'])
            self.assertEqual(room['preference_labels'], 0)
        self.assertIn('Living Room', markdown(result))

    def test_missing_evaluation_and_denominator(self):
        report = fixture('Living Room')
        report['routine_baseline']['evaluation']['hourly_brier'] = None
        result = compare([fixture('Office'), report])
        self.assertIsNone(result['rooms'][1]['hourly_better_in_this_sample'])
        self.assertIsNone(fraction(0, 0))

    def test_mismatched_dates_and_same_room_rejected(self):
        report = fixture('Living Room')
        report['end'] = '2026-10-06T00:00:00-04:00'
        with self.assertRaises(ValueError):
            compare([fixture('Office'), report])
        with self.assertRaises(ValueError):
            compare([fixture('Office'), fixture('Office')])
