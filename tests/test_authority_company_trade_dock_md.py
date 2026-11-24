"""Structural regression for the isolated trade dock cue sequencing."""
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


class CompanyTradeDockMDTests(unittest.TestCase):
    def test_dock_stages_read_retained_state_after_parent_actions(self):
        root = ET.parse(ROOT / 'tools/dev/authority_company_trade_dock.xml').getroot()
        prepare = root.find("./cues/cue[@name='Prepare']")
        order = prepare.find("./cues/cue[@name='Order']")
        first = order.find("./cues/cue[@name='FirstObservation']")
        later = first.find("./cues/cue[@name='LaterObservation']")

        self.assertEqual(prepare.get('namespace'), 'this')
        self.assertEqual([cue.get('instantiate') for cue in (prepare, order, first, later)],
                         [None, None, None, None])
        self.assertFalse(list(prepare.iter('reset_cue')))
        self.assertFalse(list(prepare.iter('event_cue_completed')))

        for cue, stage in ((order, 'CAPTAIN_ASSIGNED'), (later, 'DOCKED_ONCE')):
            with self.subTest(cue=cue.get('name')):
                self.assertEqual(cue.get('checkinterval'), '1s')
                self.assertEqual(cue.find('./delay').get('exact'), '1s')
                conditions = cue.findall('./conditions/*')
                self.assertEqual([(condition.tag, condition.get('value')) for condition in conditions],
                                 [('check_value', f"@Prepare.$Status == '{stage}'")])
