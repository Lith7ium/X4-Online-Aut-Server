"""Exercise the private import and receipt guard without X4."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/dev'))
from authority_cockpit_purchase import (
    PURCHASE, REGISTRY_EPOCH, REQUIRED_METADATA, cockpit_receipt, load_cockpit_capture, read_cockpit_purchase,
)
from authority_player_dock_observation import CONFIRM_PATH, verify_player_flight


NONCE = 'a' * 32
INTENT = {**PURCHASE, 'price_cents': 5819}


class CockpitPurchaseTest(unittest.TestCase):
    def test_import_rebuilds_capture_metadata_and_uses_same_bytes_for_digest(self):
        origin = 'IN_SPACE|0x10|0x20|0|0|0|0x40|0|374528|0|1000000|300.11'
        arrived = 'DOCKED|0x10|0x20|0x30|0x10|0x10|0x10|0|2364|0|1000000|550.31'
        rows = [
            'START|' + origin, 'SEAT|controlled|16ULL', 'AUTOPILOT|started|nil', 'DEPARTURE|374528|373123',
            'QUICKDOCK|dispatched', 'DOCKED|' + arrived, 'BEFORE_CONFIRM|' + arrived,
            'CONFIRM_PATH|' + CONFIRM_PATH, 'AFTER_CONFIRM|' + arrived,
            'INTENT|cluster_01_sector001_macro|BIM-169|water|6|5819', 'DONE|intent_captured',
        ]
        document = {
            **REQUIRED_METADATA, 'scope': 'private stock cockpit flight and docking diagnostic',
            'trial_nonce': NONCE, 'experiment': verify_player_flight(rows),
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'result.json'
            data = json.dumps(document).encode()
            path.write_bytes(data)
            self.assertEqual(load_cockpit_capture(path), (INTENT, NONCE, hashlib.sha256(data).hexdigest()))
            self.assertEqual(read_cockpit_purchase(path), INTENT)
            cases = (
                {**document, 'sha256': 'b' * 64}, {**document, 'success': 1},
                {**document, 'trial_nonce': ''}, {**document, 'ship_index': 1970},
                {**document, 'manual': True}, {**document, 'published': True},
                {**document, 'experiment': {**document['experiment'], 'unit_price_cents': 1}},
                {**document, 'experiment': {**document['experiment'],
                     'records': [row.replace(CONFIRM_PATH, 'direct_callback') for row in rows]}},
            )
            for invalid in cases:
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    path.write_text(json.dumps(invalid))
                    load_cockpit_capture(path)
            path.write_bytes(b' ' * ((1 << 20) + 1))
            with self.assertRaises(ValueError):
                load_cockpit_capture(path)

    def test_verified_native_settlement_translates_absolute_state_and_debit(self):
        rows = [
            'START', f'DOCK|{NONCE}|READY|0x10|0x20|0x30|447.644',
            f'RESULT|{NONCE}|quote|5819', f'RESULT|{NONCE}|submitted|5819',
            f'DEAL|{NONCE}|{REGISTRY_EPOCH}|six_unit|6|5819|448.030|455.660|0x10|0x20|water|6|0'
            '|5819|34914|1000000|965100|1|1|1000000|965100|2|8',
            f'OBSERVED|{NONCE}|5819|6|-6|0|0|-34900|1|1',
            f'RESULT|{NONCE}|already_attempted|0', 'LEDGER|observed|-|75|448.4|455.7',
            'ENTRY|ship|455.65|16|32|32|water|6|1|73|75|trade|0|completed_trade|1|1|-34900|5819|1',
            'DONE',
        ]
        receipt = cockpit_receipt(INTENT, rows, NONCE, REGISTRY_EPOCH)
        self.assertEqual({k: receipt[k] for k in INTENT}, INTENT)
        self.assertEqual(receipt['outcome'], 'confirmed')
        self.assertEqual(receipt['settled_total_cents'], 34900)
        self.assertEqual(receipt['company_balance_cents'], 965100)
        self.assertEqual(receipt['cargo_units'], 8)
        self.assertTrue(receipt['native_evidence']['verified'])
        self.assertEqual(receipt['native_evidence']['records'], rows)
        with self.assertRaisesRegex(ValueError, 'does not confirm'):
            cockpit_receipt({**INTENT, 'price_cents': 5820}, rows, NONCE, REGISTRY_EPOCH)
        rows[4] = rows[4].removesuffix('|2|8')
        with self.assertRaisesRegex(ValueError, 'missing native ship cargo'):
            cockpit_receipt(INTENT, rows, NONCE, REGISTRY_EPOCH)

    def test_refusal_binds_independent_quote_current_price_and_unchanged_state(self):
        rows = [
            'START', f'DOCK|{NONCE}|READY|0x10|0x20|0x30|447.644',
            f'RESULT|{NONCE}|quote|5819', f'RESULT|{NONCE}|price_changed|5820',
            f'REFUSED|{NONCE}|price_changed|5819|5820|2|2|1000000|1000000', 'DONE',
        ]
        receipt = cockpit_receipt(INTENT, rows, NONCE, REGISTRY_EPOCH)
        self.assertEqual({k: receipt[k] for k in INTENT}, INTENT)
        self.assertEqual(receipt['outcome'], 'refused')
        self.assertNotIn('settled_total_cents', receipt)
        self.assertEqual(receipt['native_evidence']['current_unit_cents'], 5820)
        replacement_cases = (
            (1, rows[1].replace('0x20', '0x10')),
            (3, rows[3].replace('5820', '5819')),
            (4, rows[4].replace('|2|2|', '|2|8|')),
            (4, rows[4].removesuffix('1000000') + '965100'),
            (4, rows[4].replace(NONCE, 'b' * 32)),
        )
        for index, row in replacement_cases:
            invalid = rows[:index] + [row] + rows[index + 1:]
            with self.subTest(row=row), self.assertRaises(ValueError):
                cockpit_receipt(INTENT, invalid, NONCE, REGISTRY_EPOCH)
        with self.assertRaises(ValueError):
            cockpit_receipt(INTENT, rows[:-1] + ['OBSERVED|unexpected', 'DONE'], NONCE, REGISTRY_EPOCH)
        with self.assertRaises(ValueError):
            cockpit_receipt(INTENT, rows, NONCE, 'b' * 32)
