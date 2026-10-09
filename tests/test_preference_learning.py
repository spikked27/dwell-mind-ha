import copy
import random
import unittest
from policy import SafeError
from preference_learning import train_preferences


def reviews():
    rng=random.Random(571);result=[]
    for i in range(300):
        value=rng.choice(['on','off'])
        result.append({'source':'explicit_human_review','decision_id':'review-'+str(i),'time':1775001600000+i*3600000,
                       'x':{'sensor.study_lux|value|lx':10 if value=='on' else 500},'y':value,'current':'off' if value=='on' else 'on'})
    return result


class PreferenceTests(unittest.TestCase):
    def test_explicit_review_learning_and_independent_test(self):
        rows=reviews();result=train_preferences(rows)
        self.assertEqual(result['source'],'explicit_human_review')
        self.assertEqual(result['state'],'validated_preference_candidate')
        self.assertLess(result['held_out']['learned']['brier'],result['held_out']['prevalence']['brier'])
        changed=copy.deepcopy(rows)
        for e in changed[250:]:e['y']='off' if e['y']=='on' else 'on'
        self.assertEqual(result['model'],train_preferences(changed)['model'])

    def test_automation_duplicate_small_or_mixed_units_cannot_become_preferences(self):
        rows=reviews()
        for alter in [lambda r:r[0].update(source='automation'),lambda r:r[0].update(decision_id=r[1]['decision_id']),lambda r:r[0].update(unit='different')]:
            changed=copy.deepcopy(rows);alter(changed)
            with self.assertRaises(SafeError):train_preferences(changed)
        with self.assertRaises(SafeError):train_preferences(rows[:20])
