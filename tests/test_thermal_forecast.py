import copy
from datetime import datetime, timezone
import math
import unittest

from policy import SafeError
from thermal_forecast import HOUR, DAY, fit, iso, samples, train, forecast_window


def dataset():
    start = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp()*1000)
    rows = [{'start':start+i*HOUR, 'mean':70+2*math.sin(i*2*math.pi/24)+3*math.sin(i/90)}
            for i in range(24*200)]
    return {'entity_id':'sensor.study_temperature', 'unit':'°F', 'start':iso(start),
            'end':iso(start+200*DAY), 'move_date':iso(start+30*DAY), 'rows':rows}


class ThermalTests(unittest.TestCase):
    def test_live_forecast_requires_recent_complete_same_home_window(self):
        data=dataset();result=train(data);result['model_id']='synthetic'
        rows=data['rows'][-25:];now=rows[-1]['start']+HOUR
        value=forecast_window(result,rows,now)
        self.assertEqual(value['target_start'],iso(now))
        self.assertEqual(len(value['observed_hours']),25)
        reconstructed=value['persistence_mean']+value['intercept_change']+sum(c['change'] for c in value['contributions'])
        self.assertAlmostEqual(reconstructed,value['predicted_mean'],places=3)
        for broken in [rows[:-1],rows[:-1]+[dict(rows[-1],mean=None)],rows[:-1]+[rows[0]]]:
            with self.assertRaises(SafeError):forecast_window(result,broken,now)
        with self.assertRaises(SafeError):forecast_window(result,rows,now+3*HOUR)
        with self.assertRaises(SafeError):forecast_window(result,rows,now-HOUR)
        out=copy.deepcopy(rows)
        for r in out:r['mean']=130
        self.assertEqual(forecast_window(result,out,now)['state'],'abstained')

    def test_learned_forecast_beats_baselines_on_unseen_synthetic_history(self):
        result = train(dataset())
        self.assertEqual(result['evaluation_status'], 'improves_baselines')
        self.assertLess(result['held_out_test']['learned']['mae'], result['held_out_test']['persistence']['mae'])
        self.assertFalse(result['control_enabled'])
        self.assertEqual(result['preference_labels'], 0)

    def test_final_test_changes_cannot_change_selection_scaling_or_coefficients(self):
        original = dataset()
        changed = copy.deepcopy(original)
        cutoff = changed['rows'][-1]['start']-30*DAY+HOUR
        for row in changed['rows']:
            if row['start'] >= cutoff:
                row['mean'] += 10
        a, b = train(original), train(changed)
        self.assertEqual(a['selected_on_validation'], b['selected_on_validation'])
        self.assertEqual(a['model'], b['model'])
        self.assertEqual(a['validation_candidates'], b['validation_candidates'])
        self.assertNotEqual(a['held_out_test'], b['held_out_test'])

    def test_no_interpolation_across_missing_hour_or_move_boundary(self):
        value = dataset()
        absent = value['rows'][200]['start']
        value['rows'][200]['mean'] = None
        examples, coverage, _, _, move = samples(value)
        self.assertEqual(coverage['invalid_or_missing_means'], 1)
        for example in examples:
            self.assertFalse(example['time']-24*HOUR <= absent <= example['time']+HOUR)
            self.assertFalse(example['time']-24*HOUR < move <= example['time']+HOUR)

    def test_duplicate_malformed_unit_and_unbounded_input_refused(self):
        value = dataset()
        for transform in [lambda d:d.update(unit='unknown'),
                          lambda d:d['rows'].append(d['rows'][0]),
                          lambda d:d['rows'][0].update(start=1),
                          lambda d:d.update(move_date=d['end'])]:
            changed = copy.deepcopy(value)
            transform(changed)
            with self.assertRaises(SafeError):
                train(changed)

    def test_training_and_validation_targets_end_before_test_features(self):
        result = train(dataset())
        self.assertLessEqual(result['model']['training_last_target_end'], result['validation_start'])
        self.assertGreater(result['held_out_test']['learned']['examples'], 200)
