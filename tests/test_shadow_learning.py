import copy
from datetime import datetime, timedelta, timezone
import random
import unittest

from policy import SafeError
from shadow_learning import train, infer, features, propose
from shadow_archive import snapshots, project


def dataset(count=2400):
    rng=random.Random(617);signals=[rng.choice(['on','off']) for _ in range(count+1)]
    start=datetime(2026,4,1,tzinfo=timezone.utc);rows=[]
    for i in range(count):
        timestamp=(start+timedelta(minutes=5*i)).isoformat()
        def row(state,**extra):return {'state':state,'room':'Study','availability':'reported','segment':'verified','time':timestamp,'actor':'automation','record_kind':'snapshot',**extra}
        rows.append({'time':timestamp,'observations':{
            'light.study':row(signals[i-1],attributes={'brightness':128}),
            'binary_sensor.study':row(signals[i],device_class='occupancy'),
            'sensor.lux':row('numeric',value=10 if signals[i]=='on' else 500,unit='lx',device_class='illuminance')}})
    return rows


class ShadowLearningTests(unittest.TestCase):
    def test_actual_learning_beats_all_baselines_on_unseen_synthetic_behavior(self):
        rows=dataset();result=train(rows,'2026-03-20T04:00:00Z',set(rows[0]['observations']))
        report=result['reports']['light.study|state']
        self.assertEqual(report['state'],'beats_baselines')
        self.assertLess(report['held_out']['learned']['brier'],min(report['held_out'][k]['brier'] for k in ['hourly','prevalence','persistence']))
        self.assertEqual(report['preference_labels'],0)
        self.assertEqual(report['actor_counts'],{'automation':2399})
        self.assertFalse(result['control_enabled'])

    def test_holdout_cannot_change_model_selection_calibration_or_preprocessing(self):
        rows=dataset();changed=copy.deepcopy(rows)
        for row in changed[2000:]:row['observations']['light.study']['state']='on' if row['observations']['light.study']['state']=='off' else 'off'
        allowed=set(rows[0]['observations'])
        a=train(rows,'2026-03-20T04:00:00Z',allowed);b=train(changed,'2026-03-20T04:00:00Z',allowed)
        self.assertEqual(a['models']['light.study|state']['model'],b['models']['light.study|state']['model'])
        self.assertEqual(a['reports']['light.study|state']['selected_on_validation'],b['reports']['light.study|state']['selected_on_validation'])
        self.assertNotEqual(a['reports']['light.study|state']['held_out'],b['reports']['light.study|state']['held_out'])

    def test_gaps_old_home_and_constant_targets_do_not_fake_training(self):
        rows=dataset(400)
        for i,row in enumerate(rows):
            for value in row['observations'].values():value['segment']=str(i)
        self.assertEqual(train(rows,'2026-03-20T04:00:00Z',set(rows[0]['observations']))['models'],{})
        rows=dataset(400)
        for row in rows:row['observations']['light.study']['state']='off'
        result=train(rows,'2026-03-20T04:00:00Z',set(rows[0]['observations']))
        self.assertEqual(result['reports']['light.study|state']['state'],'insufficient_variation')
        with self.assertRaises(SafeError):train(rows,'2026-03-20T04:00:00Z',{'light.study'})

    def test_shadow_guards_and_sensor_predictions_have_no_execution(self):
        rows=dataset();result=train(rows,'2026-03-20T04:00:00Z',set(rows[0]['observations']))
        model=result['models']['light.study|state'];obs=rows[-1]['observations'];now=int(datetime.fromisoformat(rows[-1]['time']).timestamp()*1000)
        prediction=infer(model['model'],features(obs,'Study',now))
        policy={'manual_hold_seconds':1800,'night':True,'night_brightness_max':51,'climate_min':18,'climate_max':24}
        decision=propose('light.study|state',model,prediction,'off',obs,policy,now)
        self.assertFalse(decision['executed']);self.assertFalse(decision['desired_action_learned'])
        self.assertIn('execution_not_implemented',decision['blocked_by'])
        obs['light.study'].update(actor='user_associated',record_kind='state_update')
        self.assertIn('manual_priority_hold',propose('x',model,prediction,'off',obs,policy,now)['blocked_by'])
        bright=copy.deepcopy(model);bright['channel']='brightness';prediction['prediction']='255'
        self.assertIn('nighttime_brightness_limit',propose('x',bright,prediction,'128',obs,policy,now)['blocked_by'])

    def test_archive_projection_never_invents_attribution_or_indefinite_availability(self):
        meta={'room':'Study','unit':None};start=datetime(2026,4,1,tzinfo=timezone.utc)
        events=[('light.study',project('light.study',meta,{'time':start.isoformat(),'state':1,'brightness':200})),
                ('sensor.lux',project('sensor.lux',{'room':'Study','unit':'lx'}, {'time':(start+timedelta(hours=3)).isoformat(),'value':50}))]
        rows=snapshots(events)
        self.assertTrue(rows)
        self.assertTrue(all(datetime.fromisoformat(r['time'])<=start+timedelta(minutes=30) for r in rows))
        self.assertTrue(all(v['actor']=='unattributed' for r in rows for v in r['observations'].values()))
