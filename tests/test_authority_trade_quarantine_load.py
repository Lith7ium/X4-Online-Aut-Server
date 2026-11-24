from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/dev'))
from authority_company_trade_trial import loaded_observation
from authority_trade_quarantine_load import verify_observation


class QuarantinedTradeObservations(unittest.TestCase):
    def setUp(self):
        self.rows = [
            'BEGIN age=469 ware=water',
            'ACCOUNT faction=player faction_ct=965100 player_ct=965100',
            'SHIP index=1970 code=WOO-831 status=observed sector=cluster_14_sector001_macro '
            'docked=0 internal=0 dock_state=none dock_host=none free_units=90 cargo_units=0',
            'SHIP index=7354 code=WRN-302 status=observed sector=cluster_01_sector001_macro '
            'docked=1 internal=0 dock_state=occupied dock_host=BIM-169 free_units=19 cargo_units=6',
            'AVATAR station=XPJ-169 sector=cluster_06_sector002_macro room=dockingbay_arg_s_01_macro '
            'occupied=0 controlled=0',
            'END',
        ]

    def test_general_parser_preserves_avatar_receipt(self):
        log = ''.join(f'[X4LAN] AUTH_TRADE_READ {row}\n' for row in self.rows).encode('ascii')
        rows = loaded_observation(None, lambda _path, _limit: log)
        self.assertEqual(rows, self.rows)
        verify_observation(rows)

    def test_rejects_missing_duplicate_or_changed_control_ship(self):
        control = self.rows[2]
        alternatives = [
            [],
            [control, control],
            [control.replace('cargo_units=0', 'cargo_units=1')],
            [control.replace('free_units=90', 'free_units=89')],
            [control.replace('cluster_14_sector001_macro', 'cluster_01_sector001_macro')],
        ]
        for rows in alternatives:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                verify_observation(self.rows[:2] + rows + self.rows[3:])
