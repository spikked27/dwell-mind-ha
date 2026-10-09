import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from capture_summary import ReportReader, project_report
from policy import SafeError


def report():
    return {'start':'2026-10-01T00:00:00Z','end':'2026-10-01T00:05:00Z','stop_reason':'duration',
            'rows':3,'control_enabled':False,'preference_labels':0,'all_entities_have_terminal_gap':True,
            'entities':{'light.study':{'room':'Study','rows':3,'reported_state_coverage_percent':100,
                         'record_kinds':{'snapshot':1,'gap':2},'light_update_actor_counts':{},'terminal_gap_present':True}}}


class ProjectionTests(unittest.TestCase):
    def test_duration_coverage_and_no_unreviewed_private_fields(self):
        value=report();value['secret']='must-not-leak';value['entities']['light.study']['raw_context']='must-not-leak'
        summary=project_report(value,'capture-'+'a'*32)
        self.assertEqual(summary['duration_seconds'],300)
        self.assertEqual(summary['mean_entity_coverage_percent'],100)
        self.assertNotIn('must-not-leak',json.dumps(summary))

    def test_unknown_devices_remain_unknown_and_are_not_removed(self):
        value=report();value['entities']['light.study']['reported_state_coverage_percent']=0
        summary=project_report(value,'capture-'+'a'*32)
        self.assertEqual(summary['zero_coverage_entities'],['light.study'])
        self.assertIn('light.study',summary['entities'])

    def test_controls_or_malformed_coverage_are_rejected(self):
        for changes in [{'control_enabled':True},{'preference_labels':1},{'rows':-1}]:
            with self.assertRaises(SafeError):project_report({**report(),**changes},'capture-'+'a'*32)
        for coverage in [float('nan'),101,'100',True]:
            value=report();value['entities']['light.study']['reported_state_coverage_percent']=coverage
            with self.assertRaises(SafeError):project_report(value,'capture-'+'a'*32)


@unittest.skipUnless(hasattr(os,'geteuid'),'Private report files require POSIX permissions')
class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)

    def save(self,name,value):
        path=self.folder/name;path.write_text(json.dumps(value));path.chmod(0o600);return path

    def test_restore_latest_by_capture_end_and_preserve_file_bytes(self):
        first=self.save('capture-'+'a'*32+'.json',report())
        latest=report();latest['start']='2026-10-02T00:00:00Z';latest['end']='2026-10-02T00:05:00Z'
        second=self.save('capture-'+'b'*32+'.json',latest)
        os.utime(first,None) # Restore/copy time must not override actual capture chronology.
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.folder.iterdir()}
        result=ReportReader(self.folder).latest()
        self.assertEqual(result['report_id'],second.stem)
        self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.folder.iterdir()})

    def test_symlink_or_public_report_refused_without_modifying_it(self):
        path=self.save('capture-'+'a'*32+'.json',report());path.chmod(0o644)
        with self.assertRaises(SafeError):ReportReader(self.folder).latest()
        self.assertEqual(path.stat().st_mode&0o777,0o644)
        path.chmod(0o600)
        (self.folder/('capture-'+'b'*32+'.json')).symlink_to(path)
        with self.assertRaises(OSError):ReportReader(self.folder).latest()

