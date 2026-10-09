import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('area_selection',Path(__file__).resolve().parents[1]/'custom_components/dwellmind/selection.py')
selection=importlib.util.module_from_spec(spec);spec.loader.exec_module(selection)


class DiscoveryTests(unittest.TestCase):
    def test_explicit_area_overrides_device_and_child_inherits_parent(self):
        devices={'a':{'area_id':'office'},'child':{'via_device_id':'a'}}
        self.assertEqual(selection.effective_area({'area_id':'living','device_id':'a'},devices),'living')
        self.assertEqual(selection.effective_area({'device_id':'child'},devices),'office')
        self.assertIsNone(selection.effective_area({'device_id':'loop'},{'loop':{'via_device_id':'loop'}}))

    def test_unavailable_lights_retained_disabled_diagnostic_and_wrong_classes_excluded(self):
        entities=[{'entity_id':'light.offline','device_id':'d'},
                  {'entity_id':'light.disabled','device_id':'d','disabled_by':'user'},
                  {'entity_id':'light.status_led','device_id':'d','entity_category':'config'},
                  {'entity_id':'binary_sensor.motion','device_id':'d','device_class':'motion'},
                  {'entity_id':'binary_sensor.door','device_id':'d','device_class':'door'},
                  {'entity_id':'sensor.temp','device_id':'d','device_class':'temperature'},
                  {'entity_id':'light.other','area_id':'other','device_id':'d'}]
        found=selection.discover({'office'},entities,{'d':{'area_id':'office'}},{})
        self.assertEqual(set(found),{'light.offline','binary_sensor.motion','binary_sensor.door','sensor.temp'})

