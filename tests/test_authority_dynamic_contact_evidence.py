"""Structural and synthetic regressions for the private diagnostic."""
import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

DEV = Path(__file__).resolve().parents[1] / 'tools/dev'
sys.path.insert(0, str(DEV))

import authority_dynamic_contact_evidence as evidence


BASE = evidence.REFERENCE + 'baseline|1|0x1|0x2|100'
FIRST = evidence.MARKER + 'first_direct|1|2|0x1|0x2|7|0x2|components|0x1|100|100'
FIRST_DEBIT = evidence.REFERENCE + 'shield|2|0x2|0x2|26|0x1|74'
SECOND_DEBIT = evidence.REFERENCE + 'shield|2.1|0x2|0x2|26|0x1|48'
DYNAMIC_FIRST = evidence.MARKER + 'shield|2|2|0x1|0x2|7|0x2|26|0x1|74|100'
DYNAMIC_SECOND = evidence.MARKER + 'shield|3|2.1|0x1|0x2|7|0x2|26|0x1|48|100'


class DynamicContactEvidence(unittest.TestCase):
    def test_first_and_later_debits_are_distinct(self):
        result = evidence.summarize(
            [BASE, FIRST, FIRST_DEBIT, DYNAMIC_FIRST, SECOND_DEBIT, DYNAMIC_SECOND])
        self.assertTrue(result['first_debit_seen'])
        self.assertEqual((result['first_direct_line'], result['first_debit_line']), (2, 3))
        self.assertEqual(result['counts']['shield'], 2)
        self.assertEqual(result['missing_damage_events'], 0)

    def test_debit_before_subscription_is_reported_as_missing(self):
        later = DYNAMIC_SECOND.replace('shield|3|', 'shield|2|')
        result = evidence.summarize([BASE, FIRST_DEBIT, FIRST, SECOND_DEBIT, later])
        self.assertTrue(result['first_debit_precedes_subscription'])
        self.assertFalse(result['first_debit_seen'])
        self.assertEqual(result['missing_damage_events'], 1)

    def test_actor_index_and_sequence_are_pinned(self):
        for bad in (DYNAMIC_FIRST.replace('|7|', '|8|'),
                    DYNAMIC_FIRST.replace('0x1|0x2|7', '0x4|0x2|7'),
                    DYNAMIC_FIRST.replace('0x1|0x2|7', '0x1|0x3|7'),
                    DYNAMIC_FIRST.replace('shield|2|', 'shield|3|')):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'sequence or registered identity'):
                evidence.summarize([BASE, FIRST, FIRST_DEBIT, bad])

    def test_watcher_only_adds_a_target_from_the_direct_event(self):
        root = ET.parse(DEV / 'authority_dynamic_contact_watch.xml').getroot()
        cues = list(root.iter('cue'))
        direct = next(cue for cue in cues if cue.get('name') == 'Direct')
        target_additions = root.findall(".//add_to_group[@groupname='State.$targets']")
        self.assertEqual(target_additions, direct.findall(".//add_to_group[@groupname='State.$targets']"))
        self.assertEqual([node.get('object') for node in target_additions], ['$target'])
        self.assertFalse(any(node.tag.startswith(('set_object_', 'apply_', 'find_'))
                             or node.tag == 'raise_lua_event' for node in root.iter()))
