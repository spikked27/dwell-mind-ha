from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from container_app import environment_config, load_token, private_directory, run
from policy import SafeError
from rooms import OFFICE, configured_profiles
from test_live_observer import Clock, FakeWS
from ha_live_observer import Capture


class ContainerTests(unittest.TestCase):
    def env(self, **changes):
        return {'HA_URL':'https://ha.example.invalid','HA_TOKEN':'dummy-test-token-000000000000000000000000',
                'ROOMS':'office','CAPTURE_SECONDS':'10','MAX_RECONNECTS':'0',**changes}

    def test_environment_accepts_custom_roles_without_token_in_config(self):
        config=environment_config(self.env(OFFICE_PRIMARY_LIGHT='light.study',OFFICE_EXTRA_LIGHT='light.reading'))
        self.assertEqual(config.profiles[0].primary_light,'light.study')
        self.assertNotIn('dummy-test-token',str(vars(config)))

    def test_invalid_boolean_number_domain_and_duplicate_roles(self):
        for changes in [{'ALLOW_PLAINTEXT_HA':'yes'},{'CAPTURE_SECONDS':'-1'},
                        {'OFFICE_PRIMARY_LIGHT':'switch.study'},{'OFFICE_PRIMARY_LIGHT':OFFICE.extra_light},
                        {'HA_URL':'http://localhost:8123'}, {'ROOMS':'office,office'}]:
            with self.assertRaises((SafeError,ValueError)):
                environment_config(self.env(**changes))

    def test_disable_optional_tv(self):
        config=environment_config(self.env(ROOMS='living_room',LIVING_ROOM_MEDIA=''))
        self.assertIsNone(config.profiles[0].media)
        self.assertEqual(len(config.profiles[0].entities),6)

    def test_token_sources_are_exclusive_and_never_in_error(self):
        token=self.env()['HA_TOKEN']
        self.assertEqual(load_token({'HA_TOKEN':token}),token)
        for env in [{},{'HA_TOKEN':token,'HA_TOKEN_FILE':'/some/file'},{'HA_TOKEN':'bad\nvalue'}]:
            with self.assertRaises(SafeError) as caught:
                load_token(env)
            self.assertNotIn(token,str(caught.exception))

    def test_private_appdata_does_not_change_existing_permissions(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('DWELLMIND_TEST_TMPDIR')) as directory:
            child=Path(directory)/'old'
            child.mkdir(mode=0o755)
            child.chmod(0o755)
            with self.assertRaises(SafeError):
                private_directory(child)
            self.assertEqual(stat.S_IMODE(child.stat().st_mode),0o755)

    def test_container_to_report_end_to_end_offline(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('DWELLMIND_TEST_TMPDIR')) as directory:
            clock=Clock()
            profiles=configured_profiles({'office':{'primary_light':'light.study'}})
            entities=profiles['office'].entities
            initial={e:{'s':'100' if e.startswith('sensor.') else 'off','a':{},'lc':1790812800,'c':{}} for e in entities}
            ws=FakeWS(clock,[{'type':'auth_required'},{'type':'auth_ok'},
                             {'id':1,'type':'result','success':True}, {'id':1,'type':'event','event':{'a':initial}}])
            def factory(config,token,journal):
                return Capture(config,token,journal,connector=lambda _:ws,clock=clock,sleep=clock.sleep,wall=clock.wall)
            output=io.StringIO()
            with redirect_stdout(output):
                code=run(self.env(DATA_DIR=directory,OFFICE_PRIMARY_LIGHT='light.study'),capture_factory=factory)
            self.assertEqual(code,0)
            report=next((Path(directory)/'reports').glob('*.json'))
            data=json.loads(report.read_text())
            self.assertIn('light.study',data['entities'])
            self.assertEqual(data['preference_labels'],0)
            self.assertTrue(data['all_entities_have_terminal_gap'])
            self.assertNotIn('dummy-test-token',output.getvalue()+report.read_text())
            self.assertEqual(stat.S_IMODE(report.stat().st_mode),0o600)

    def test_unraid_template_environment_matches_parser(self):
        root=ET.parse(Path(__file__).resolve().parents[1]/'unraid/dwellmind-ha.xml').getroot()
        configs=root.findall('Config')
        env={c.attrib['Target']:c.text or c.attrib.get('Default','') for c in configs if c.attrib['Type']=='Variable'}
        self.assertEqual(env['RUN_MODE'],'service')
        self.assertNotIn('HA_TOKEN',env)
        self.assertFalse(any(c.attrib['Target'].startswith(('OFFICE_','LIVING_ROOM_')) for c in configs))
        self.assertEqual(next(c for c in configs if c.attrib['Type']=='Port').attrib['Target'],'8128')
