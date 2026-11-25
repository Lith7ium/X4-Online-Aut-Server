from contextlib import chdir
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools/dev'))
from verify_boarding_witness import verify

if __package__:
    from .run_fixture_lua import run_fixture
else:
    from run_fixture_lua import run_fixture


class BoardingWitnessTests(unittest.TestCase):
    epoch = 'a' * 32
    prefix = '[X4LAN] AUTH_BOARDING_WITNESS|'

    def verify(self, *rows):
        return verify((self.prefix + row for row in rows),
                      epoch=self.epoch, index=42, target=0x23,
                      macro='ship_target_macro', company_indices={12})

    def setUp(self):
        self.arm = f'ARM|{self.epoch}|42|0x23|ship_target_macro|paranid'
        self.start = 'START|op_1|0x23|2'
        self.company = 'COMPANY|op_1|0x23|12|0x76'
        self.boarded = 'BOARDED|op_1|0x23|player'
        self.owner = 'OWNER|op_1|0x23|paranid|player'
        self.readback = 'READBACK|op_1|0x23|ship_target_macro|player'
        self.removed = 'REMOVED|op_1|0x23'

    def test_only_correlated_target_takeover_is_positive(self):
        result = self.verify(self.arm, self.start, self.company, self.boarded, self.owner,
                             self.removed, self.readback)
        self.assertEqual(result['outcome'], 'takeover_observed')
        self.assertEqual(result['company_attackers'], {12: '0x76'})
        self.assertEqual(self.verify(self.arm, self.start, self.company, self.removed)['outcome'],
                         'removed_without_confirmed_takeover')
        self.assertEqual(self.verify(self.arm, self.start, self.boarded, self.owner, self.readback)['outcome'],
                         'unattributed')

    def test_rejects_wrong_object_operation_owner_attacker_and_order(self):
        for row, message in (
            (self.boarded.replace('0x23', '0x24'), 'another target'),
            (self.owner.replace('op_1', 'op_2'), 'another operation'),
            (self.owner.replace('paranid', 'teladi'), 'true-owner'),
            (self.company.replace('|12|', '|13|'), 'Company attacker'),
            (self.readback, 'without prior'),
        ):
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, message):
                self.verify(self.arm, self.start, row)
        with self.assertRaisesRegex(ValueError, 'follows operation removal'):
            self.verify(self.arm, self.start, self.company, self.removed, self.boarded, self.owner)

    def test_preflight_converts_lua_references_and_reports_identity_failure_stage(self):
        with chdir(ROOT):
            run_fixture(ROOT / 'tests/authority_boarding_preflight.lua')

    def test_stock_event_selectors_and_no_game_actions(self):
        root = ET.parse(ROOT/'tools/dev/authority_boarding_witness.xml').getroot()
        self.assertEqual({node.tag for node in root.iter()}, {
            'mdscript', 'cues', 'cue', 'conditions', 'actions', 'set_value', 'delay',
            'event_ui_triggered', 'event_boarding_operation_started', 'event_object_signalled',
            'event_object_changed_true_owner', 'event_boarding_operation_removed',
            'check_value', 'do_if', 'debug_text', 'do_for_each', 'break',
        })
        boarded = root.find('.//event_object_signalled')
        self.assertEqual(boarded.attrib,
                         {'object': 'Arm.$target', 'param': "'boarded'", 'param2': 'faction.player'})
        self.assertEqual(root.find('.//event_object_changed_true_owner').get('object'), 'Arm.$target')
        self.assertEqual(root.find('.//event_boarding_operation_removed').get('operation'), 'Arm.$operation')
        for gate in root.iter('do_if'):
            predicate = gate.get('value', '')
            if '.$format == 3' in predicate:
                self.assertEqual(predicate.count('.$ships.'), 2)
                self.assertEqual(predicate.count('.$byship.'), 2)
