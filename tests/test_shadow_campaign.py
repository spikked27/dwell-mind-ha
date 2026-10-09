from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import time
import unittest

from shadow_campaign import Campaign, read_rows
from shadow_learning import train
from test_shadow_learning import dataset


@unittest.skipUnless(hasattr(os,'geteuid'),'Private campaign journals require POSIX permissions')
class CampaignTests(unittest.TestCase):
    def test_prediction_precedes_real_outcome_and_gaps_never_count_as_accuracy(self):
        rows=dataset();allowed=set(rows[0]['observations']);learned=train(rows,'2026-03-20T04:00:00Z',allowed)
        clock=[int(datetime.fromisoformat(rows[-1]['time']).timestamp()*1000000000)]
        with tempfile.TemporaryDirectory() as directory:
            c=Campaign(directory,lambda:clock[0]);c.start({'days':14,'move_date':'2026-03-20','timezone':'America/New_York'},allowed)
            for _ in range(100):
                if not c.training:break
                time.sleep(.01)
            self.assertIn('-04:00',c.policy['move_date'])
            c.models={'light.study|state':learned['models']['light.study|state']}
            observations=rows[-1]['observations'];c.sample(observations,allowed)
            self.assertEqual(c.issued,1);self.assertEqual(c.evaluated,0)
            predicted=c.pending[0]['predicted'];clock[0]+=300*10**9
            observations['light.study']['state']=predicted;c.sample(observations,allowed)
            self.assertEqual(c.evaluated,1);self.assertEqual(c.correct,1)
            c.gap();self.assertEqual(c.unknown,1);self.assertEqual(c.evaluated,1)
            self.assertEqual(c.decisions[0]['outcome_status'],'unknown coverage')
            c.stop()
            logs=list(read_rows(next(c.folder.glob('decisions-*.jsonl'))))
            self.assertEqual(logs[0]['kind'],'prediction');self.assertEqual(logs[1]['kind'],'outcome')
            self.assertTrue(all(r['executed'] is False for r in logs))
            restored=Campaign(directory,lambda:clock[0]);self.assertNotEqual(restored.state,'recovery_pending')

    def test_restart_preserves_original_deadline_and_scope_blocks_old_models(self):
        clock=[int(datetime(2026,4,1,tzinfo=timezone.utc).timestamp()*1000000000)]
        with tempfile.TemporaryDirectory() as directory:
            c=Campaign(directory,lambda:clock[0]);c.start({'days':14,'move_date':'2026-03-20','timezone':'America/New_York'},{'light.study'})
            deadline=c.deadline;c.journal.close()
            clock[0]+=5*86400*10**9
            restored=Campaign(directory,lambda:clock[0]);self.assertEqual(restored.state,'recovery_pending')
            restored.resume({'light.study'});self.assertEqual(restored.deadline,deadline)
            self.assertEqual(len(list(Path(directory).glob('shadow/campaign-*'))),2)
            restored.stop()
