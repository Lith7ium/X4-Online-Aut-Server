"""Exercise the owned cockpit IPC and exact terminal receipt binding."""
from pathlib import Path
import shutil
import socket
import subprocess
import unittest

from authority.company_purchase_source import CompanyPurchaseSource


class CompanyPurchaseSourceTests(unittest.TestCase):
    def setUp(self):
        self.source = CompanyPurchaseSource(expected_epoch='a' * 32)
        self.addCleanup(self.source.close)
        self.settings = self.source.configure('a' * 32, 7354)
        self.client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.client.close)
        self.client.connect(('127.0.0.1', self.settings['port']))
        self.client.settimeout(1)
        self.peer_valid = True
        self.source.bind_validator(lambda peer: self.peer_valid and peer == self.client.getsockname())

    def send(self, sequence, price=5819, *, lease=None, ware='water'):
        lease = self.settings['lease'] if lease is None else lease
        self.client.send(f'T|{lease}|{sequence}|cluster_01_sector001_macro|BIM-169|{ware}|6|{price}'.encode())

    def receive(self):
        return self.client.recv(512).decode().split('|')[2:]

    def test_pending_retry_and_late_duplicate_remain_bound(self):
        self.send(1, lease='f' * 32)
        self.send(1, ware='Water')
        self.send(2)
        self.assertIsNone(self.source.exchange(None))
        self.peer_valid = False
        self.send(1)
        self.assertIsNone(self.source.exchange(None))
        self.peer_valid = True
        self.send(1)
        first = self.source.exchange(None)
        self.assertRegex(first['purchase_id'], r'^[0-9a-f]{32}$')
        self.assertEqual({k: v for k, v in first.items() if k != 'purchase_id'}, {
            'sector': 'cluster_01_sector001_macro', 'station_code': 'BIM-169',
            'ware': 'water', 'quantity': 6, 'price_cents': 5819,
        })
        self.send(1)
        self.send(2, 5800)
        self.assertEqual(first, self.source.exchange(None))
        refusal = dict(first, outcome='refused', reason='price_changed', current_price_cents=5800)
        self.assertEqual(first, self.source.exchange(dict(refusal, quantity=7)))
        self.assertIsNone(self.source.exchange(refusal))
        self.assertEqual(self.receive(), ['RECEIPT', '1', 'refused', 'price_changed', '5800'])
        self.send(1)
        self.assertIsNone(self.source.exchange(None))
        self.assertEqual(self.receive(), ['RECEIPT', '1', 'refused', 'price_changed', '5800'])

        self.send(2, 5800)
        second = self.source.exchange(None)
        self.assertNotEqual(second['purchase_id'], first['purchase_id'])
        self.assertEqual(second['price_cents'], 5800)
        self.assertEqual(second, self.source.exchange(refusal))
        receipt = dict(second, outcome='confirmed', settled_total_cents=34800,
                       company_balance_cents=965200, cargo_units=6, ship_code='WRN-302')
        with self.assertRaises(ValueError):
            self.source.exchange(dict(receipt, cargo_units=5))
        self.assertIsNone(self.source.exchange(receipt))
        self.assertEqual(self.receive(), ['RECEIPT', '2', 'confirmed', '34800', '965200', '6', 'WRN-302'])

    def test_unknown_remains_terminal_without_invented_balances(self):
        self.send(1)
        intent = self.source.exchange(None)
        self.assertIsNone(self.source.exchange(dict(intent, outcome='unknown')))
        self.assertEqual(self.receive(), ['RECEIPT', '1', 'unknown'])

    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')
    def test_native_queue_requires_admission_and_matches_one_receipt(self):
        root = Path(__file__).resolve().parents[1]
        subprocess.run(['luajit', 'tests/authority_company_purchase_source.lua'],
                       cwd=root, timeout=10, check=True)
