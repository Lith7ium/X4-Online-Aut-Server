"""Structure of the opt-in range observer and its non-forwarding output."""
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET


class IsolatedGunCapture(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / 'tools/dev/authority_isolated_gun_capture.xml'
        self.root = ET.parse(path).getroot()

    def test_range_queries_are_local_and_bounded(self):
        queries = [node for node in self.root.iter()
                   if node.tag in {'find_ship', 'find_station', 'find_object'}]
        self.assertEqual(len(queries), 8)
        self.assertEqual({node.get('class') for node in queries if node.tag == 'find_object'},
                         {'class.bullet', 'class.missile'})
        self.assertTrue(all(query.find("match_distance[@max='10km']") is not None for query in queries))
        self.assertEqual({node.get('space') for node in queries}, {'$source.sector', '$target.sector'})

    def test_quiet_window_and_positive_projectile_required_for_watermark(self):
        cue = self.root.find(".//cue[@name='Watermark']")
        guard = cue.find('./actions/do_if').get('value')
        self.assertIn('State.$positive_query', guard)
        self.assertIn('State.$prerolled', guard)
        self.assertIn('State.$frame_age + 0.025s', guard)
        self.assertIn('State.$expires', guard)
        self.assertIsNotNone(cue.find(".//include_actions[@ref='Range']"))
        event = cue.find('.//raise_lua_event').get('name')
        self.assertEqual(event, "'X4PrivateIsolatedGunCapture.ObservationWatermark'")
        preread = self.root.find(".//cue[@name='RangeTick']/actions/do_elseif").get('value')
        self.assertIn('player.age - State.$quiet_started ge 3s', preread)

    def test_no_damage_or_product_forwarding(self):
        self.assertFalse(any(node.tag.startswith(('set_object_', 'apply_', 'destroy_object'))
                             for node in self.root.iter()))
        names = {entry.get('name') for entry in self.root.iter('raise_lua_event')}
        self.assertEqual(names, {f"'X4PrivateIsolatedGunCapture.{event}'"
                                 for event in ('Fact', 'Armed', 'Stopped', 'ObservationWatermark')})
        self.assertIsNotNone(self.root.find('.//event_object_collided'))
        self.assertIsNotNone(self.root.find('.//event_object_hull_damaged'))
