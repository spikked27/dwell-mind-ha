import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]/'custom_components/dwellmind'
package = types.ModuleType('scope_tests_package'); package.__path__=[str(ROOT)]
sys.modules['scope_tests_package']=package
spec=importlib.util.spec_from_file_location('scope_tests_package.scope',ROOT/'scope.py')
scope=importlib.util.module_from_spec(spec);spec.loader.exec_module(scope)


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.entities={'light.allowed':{'area_id':'living'},'light.blocked':{'device_id':'child'},
                       'sensor.average':{'area_id':'living'},'sensor.private':{'area_id':'bedroom'}}
        self.devices={'child':{'via_device_id':'parent'},'parent':{'area_id':'bedroom'}}

    def blocked(self, entity, areas=(), entities=(), members=None):
        return scope.blocked(entity,set(areas),set(entities),self.entities,self.devices,members or {})

    def test_explicit_room_entity_and_inherited_device_membership_override_selection(self):
        self.assertTrue(self.blocked('light.allowed',entities=['light.allowed']))
        self.assertTrue(self.blocked('light.blocked',areas=['bedroom']))
        self.assertFalse(self.blocked('light.allowed',areas=['bedroom']))
        self.entities['light.blocked']['area_id']='living'
        self.assertFalse(self.blocked('light.blocked',areas=['bedroom']))

    def test_helper_cannot_indirectly_use_excluded_sensor(self):
        self.assertTrue(self.blocked('sensor.average',areas=['bedroom'],members={'sensor.average':['sensor.private']}))
        self.assertTrue(self.blocked('sensor.average',entities=['sensor.private'],members={'sensor.average':'sensor.private'}))

    def test_unresolved_membership_cycles_and_oversized_dependencies_fail_closed(self):
        self.assertTrue(self.blocked('sensor.missing',areas=['bedroom']))
        self.assertTrue(self.blocked('sensor.average',entities=['sensor.private'],members={'sensor.average':['sensor.average']}))
        self.assertTrue(self.blocked('sensor.average',entities=['sensor.private'],members={'sensor.average':['light.allowed']*65}))

    def test_offline_and_legacy_entities_not_deleted_or_automatically_denied(self):
        original=dict(self.entities)
        self.assertFalse(self.blocked('light.allowed'))
        self.assertFalse(self.blocked('sensor.missing'))
        self.assertEqual(self.entities,original)
