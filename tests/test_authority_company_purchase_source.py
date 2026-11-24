"""Exercise the owned cockpit IPC and exact terminal receipt binding."""
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

from authority.company_purchase_source import CompanyPurchaseSource


class CompanyPurchaseSourceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state_dir = directory.name
        self.source = CompanyPurchaseSource(expected_epoch='a' * 32, server_fingerprint='b' * 64,
                                            state_dir=self.state_dir)
        self.addCleanup(self.source.close)
        self.settings = self.source.configure('a' * 32, 7354)
        self.client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.client.close)
        self.client.connect(('127.0.0.1', self.settings['port']))
        self.client.settimeout(1)
        self.peer_valid = True
        self.source.bind_validator(lambda peer: self.peer_valid and peer == self.client.getsockname())

    def send(self, sequence, price=5819, *, lease=None, ware='water', total='-'):
        lease = self.settings['lease'] if lease is None else lease
        self.client.send(f'T|{lease}|{sequence}|cluster_01_sector001_macro|BIM-169|{ware}|6|{price}|{total}'.encode())

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

    def test_captain_required_survives_cockpit_ipc(self):
        self.send(1)
        intent = self.source.exchange(None)
        self.assertIsNone(self.source.exchange(dict(intent, outcome='refused', reason='captain_required')))
        self.assertEqual(self.receive(), ['RECEIPT', '1', 'refused', 'captain_required'])

    def test_statement_survives_a_fresh_session_without_resubmitting(self):
        self.send(1, total=34914)
        intent = self.source.exchange(None)
        token = intent['purchase_id']
        statement = dict(purchase_id=token, ware='water', quantity=6, settled_total_cents=34914)
        self.assertEqual(self.source.statement(token), statement)
        self.assertEqual(self.source.statement(token), statement)
        self.assertIsNone(self.source.exchange(dict(intent, outcome='accepted_pending_statement')))
        self.assertEqual(self.receive(), ['RECEIPT', '1', 'accepted_pending_statement'])
        self.send(2, total=34914)
        self.assertIsNone(self.source.exchange(None))
        result = dict(intent, outcome='pilot_reported', settled_total_cents=34914)
        self.assertFalse(self.source.statement_result(dict(result, settled_total_cents=34900)))
        self.assertFalse(self.source.statement_result(dict(result, station_code='ABC-123')))

        for fingerprint, epoch, index, expected in (
                ('b' * 64, 'a' * 32, 7354, statement), ('c' * 64, 'a' * 32, 7354, None),
                ('b' * 64, 'c' * 32, 7354, None), ('b' * 64, 'a' * 32, 1970, statement)):
            fresh = CompanyPurchaseSource(server_fingerprint=fingerprint, state_dir=self.state_dir)
            self.addCleanup(fresh.close)
            fresh.configure(epoch, index)
            self.assertEqual(fresh.statement(token), expected)
            self.assertIs(fresh.statement_result(result), expected is not None)

        with tempfile.TemporaryDirectory() as other_device:
            fresh = CompanyPurchaseSource(server_fingerprint='b' * 64, state_dir=other_device)
            self.addCleanup(fresh.close)
            fresh.configure('a' * 32, 1970)
            self.assertIsNone(fresh.statement(token))
            self.assertFalse(fresh.statement_result(result))
        other_seat = CompanyPurchaseSource(server_fingerprint='b' * 64, state_dir=self.state_dir)
        self.addCleanup(other_seat.close)
        other_seat_settings = other_seat.configure('a' * 32, 1970)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as other_pilot:
            other_pilot.connect(('127.0.0.1', other_seat_settings['port']))
            other_seat.bind_validator(lambda peer: peer == other_pilot.getsockname())
            other_pilot.send(
                f"T|{other_seat_settings['lease']}|1|cluster_01_sector002_macro|AAA-321|water|6|5819|34914".encode())
            unrelated = other_seat.exchange(None)
            self.assertTrue(other_seat.statement_result(result))
            self.assertEqual(other_seat.exchange(None), unrelated)
            other_pilot.setblocking(False)
            with self.assertRaises(BlockingIOError):
                other_pilot.recv(512)

        self.assertTrue(self.source.statement_result(result))
        self.assertTrue(self.source.statement_result(result))
        self.assertEqual(self.receive(), ['RECEIPT', '1', 'pilot_reported', '34914'])
        self.send(2, total=34914)
        self.assertNotEqual(self.source.exchange(None)['purchase_id'], token)

    def test_missing_native_total_stays_unavailable_and_callbacks_are_negotiated(self):
        self.send(1)
        token = self.source.exchange(None)['purchase_id']
        self.assertIsNone(self.source.statement(token))
        self.assertEqual(set(self.source.control_callbacks(SimpleNamespace())), {'company_purchase_exchange'})
        self.assertEqual(set(self.source.control_callbacks(SimpleNamespace(_company_purchase_statement_version=1))),
                         {'company_purchase_exchange', 'company_purchase_statement', 'company_purchase_statement_result'})

    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')
    def test_native_queue_requires_admission_and_matches_one_receipt(self):
        root = Path(__file__).resolve().parents[1]
        subprocess.run(['luajit', 'tests/authority_company_purchase_source.lua'],
                       cwd=root, timeout=10, check=True)
