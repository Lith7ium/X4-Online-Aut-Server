from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/dev'))
from authority_player_dock_observation import verify_player_flight


class PlayerDockFlightTests(unittest.TestCase):
    def setUp(self):
        origin = 'IN_SPACE|0x10|0x20|0|0|0|0x40|0|374528|0|1000000|300.11'
        arrived = 'DOCKED|0x10|0x20|0x30|0x10|0x10|0x10|0|2364|0|1000000|550.31'
        self.rows = [
            'START|' + origin, 'SEAT|controlled|16ULL', 'AUTOPILOT|started|nil', 'DEPARTURE|374528|373123',
            'QUICKDOCK|dispatched', 'DOCKED|' + arrived, 'BEFORE_CONFIRM|' + arrived,
            'AFTER_CONFIRM|' + arrived, 'INTENT|cluster_01_sector001_macro|BIM-169|water|6|5820',
            'DONE|intent_captured',
        ]

    def test_observed_pilot_dock_and_capture(self):
        result = verify_player_flight(self.rows)
        self.assertEqual(result['company_balance_cents'], 1000000)
        self.assertEqual(result['unit_price_cents'], 5820)
        self.assertFalse(result['purchase_executed'])

    def test_assigned_berth_must_be_the_same_bay_observed_while_piloting(self):
        stages = ['ROUTE|berth']
        for status in ('REQUESTED', 'ASSIGNED', 'DISPATCHED'):
            stages.append('BERTH|' + status + '|0x10|0x20|0x30|0x10|0x10|0x10|305')
        rows = self.rows[:2] + stages + self.rows[5:]
        result = verify_player_flight(rows, prepare_berth=True)
        self.assertIsNone(result['flight_game_seconds'])
        with self.assertRaises(ValueError):
            verify_player_flight(rows)
        stages[2] = stages[2].replace('0x30', '0x31')
        with self.assertRaises(ValueError):
            verify_player_flight(self.rows[:2] + stages + self.rows[5:], prepare_berth=True)

    def test_rejects_other_ship_or_cargo_changes(self):
        for original, altered in (('SEAT|controlled|16ULL', 'SEAT|controlled|17ULL'),
                                  ('AFTER_CONFIRM', self.rows[7].replace('|0|1000000|', '|6|1000000|')),
                                  ('DOCKED|DOCKED', self.rows[5].replace('0x30', '0')),
                                  ('INTENT', self.rows[8].replace('BIM-169', 'XPJ-169'))):
            with self.subTest(original=original), self.assertRaises(ValueError):
                verify_player_flight([altered if row.startswith(original) else row for row in self.rows])
