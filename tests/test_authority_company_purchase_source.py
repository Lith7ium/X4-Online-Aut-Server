"""Exercise the owned cockpit IPC and exact terminal receipt binding."""
from contextlib import chdir
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from authority.company_purchase_source import CompanyPurchaseSource


if __package__:
    from .run_fixture_lua import run_fixture
else:
    from run_fixture_lua import run_fixture


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

    def test_opt_in_undock_on_owned_purchase_ipc(self):
        self.assertNotIn('company_undock_exchange', self.source.control_callbacks(None))
        self.assertNotIn('undock_nonce', self.settings)
        with self.assertRaises(RuntimeError):
            self.source.undock_exchange(None)

        source = CompanyPurchaseSource(undock=True)
        self.addCleanup(source.close)
        config = source.configure('a' * 32, 7354)
        self.client.connect(('127.0.0.1', config['port']))
        source.bind_validator(lambda peer: self.peer_valid and peer == self.client.getsockname())
        exchange = source.control_callbacks(None)['company_undock_exchange']
        intent = dict(request_id=config['undock_request'], sector='cluster_01_sector001_macro',
                      station_code='BIM-169', bay_seed='507326418098681806',
                      bay_macro='bay_macro', dockarea_macro='area_macro')
        packet = f"T|{config['lease']}|UNDOCK|" + '|'.join(intent.values())
        self.peer_valid = False
        self.client.send(packet.encode())
        self.assertIsNone(exchange(None))
        self.peer_valid = True
        self.client.send(packet.replace(intent['bay_seed'], '18446744073709551616').encode())
        self.assertIsNone(exchange(None))
        self.client.send(packet.encode())
        self.assertEqual(exchange(None), intent)
        self.assertEqual(exchange(dict(intent, bay_seed='9', outcome='confirmed')), intent)
        self.send(1, lease=config['lease'])
        self.assertIsNotNone(source.exchange(None))
        self.assertIsNone(exchange(dict(intent, outcome='confirmed')))
        self.assertEqual(self.receive(), ['UNDOCK_RESULT', 'confirmed', *intent.values()])
        self.client.send(packet.encode())
        self.assertIsNone(exchange(None))
        self.assertEqual(self.receive(), ['UNDOCK_RESULT', 'confirmed', *intent.values()])

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

    def test_clean_booking_refusals_survive_cockpit_ipc(self):
        reasons = ('captain_required', 'dock_mismatch', 'provider_busy', 'no_unique_offer', 'not_tradeable')
        for sequence, reason in enumerate(reasons, 1):
            with self.subTest(reason=reason):
                self.send(sequence)
                intent = self.source.exchange(None)
                self.assertIsNone(self.source.exchange(dict(intent, outcome='refused', reason=reason)))
                self.assertEqual(self.receive(), ['RECEIPT', str(sequence), 'refused', reason])

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

    def test_reconciled_notice_after_ship_change_and_lost_ack(self):
        self.send(1, total=34914)
        original = self.source.exchange(None)
        purchase_id = original['purchase_id']
        result = dict(original, outcome='pilot_reported', settled_total_cents=34914)
        self.source.close()

        def open_cockpit(index):
            source = CompanyPurchaseSource(server_fingerprint='b' * 64, state_dir=self.state_dir)
            self.addCleanup(source.close)
            config = source.configure('a' * 32, index)
            cockpit = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.addCleanup(cockpit.close)
            cockpit.connect(('127.0.0.1', config['port']))
            cockpit.setblocking(False)
            source.bind_validator(lambda peer: peer == cockpit.getsockname())
            return source, cockpit, f"T|{config['lease']}|".encode()

        source, cockpit, prefix = open_cockpit(1970)
        cockpit.send(prefix + b'NOTICE_POLL')
        self.assertIsNone(source.exchange(None))
        cockpit.send(prefix + b'1|cluster_01_sector002_macro|AAA-321|water|1|6000|6000')
        new_basket = source.exchange(None)
        self.assertNotEqual(new_basket['purchase_id'], purchase_id)
        self.assertTrue(source.statement_result(result))
        notice = f'NOTICE|1|{purchase_id}|water|6|BIM-169|34914'.encode()
        ack = f'NOTICE_ACK|1|{purchase_id}'.encode()
        cockpit.send(prefix + b'NOTICE_POLL')
        self.assertEqual(source.exchange(None), new_basket)
        self.assertEqual(cockpit.recv(512), prefix + notice)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as wrong_peer:
            wrong_peer.sendto(prefix + ack, cockpit.getpeername())
        cockpit.send(prefix + f'NOTICE_ACK|2|{purchase_id}'.encode())
        cockpit.send(prefix + b'NOTICE_POLL')
        self.assertEqual(source.exchange(None), new_basket)
        self.assertEqual(cockpit.recv(512), prefix + notice)
        source.close()

        # The participant acknowledged the server result before the cockpit
        # acknowledged its display. Recover it from disk without another order.
        restored, cockpit, fresh_prefix = open_cockpit(1970)
        cockpit.send(fresh_prefix + b'NOTICE_POLL')
        self.assertIsNone(restored.exchange(None))
        self.assertEqual(cockpit.recv(512), fresh_prefix + notice)
        cockpit.send(prefix + ack)
        cockpit.send(fresh_prefix + b'NOTICE_POLL')
        self.assertIsNone(restored.exchange(None))
        self.assertEqual(cockpit.recv(512), fresh_prefix + notice)
        cockpit.send(fresh_prefix + ack)
        self.assertIsNone(restored.exchange(None))
        self.assertTrue(restored.statement_result(result))
        cockpit.send(fresh_prefix + b'NOTICE_POLL')
        self.assertIsNone(restored.exchange(None))
        with self.assertRaises(BlockingIOError):
            cockpit.recv(512)

    def test_missing_native_total_stays_unavailable_and_callbacks_are_negotiated(self):
        self.send(1)
        token = self.source.exchange(None)['purchase_id']
        self.assertIsNone(self.source.statement(token))
        self.assertEqual(set(self.source.control_callbacks(SimpleNamespace())), {'company_purchase_exchange'})
        self.assertEqual(set(self.source.control_callbacks(SimpleNamespace(_company_purchase_statement_version=1))),
                         {'company_purchase_exchange', 'company_purchase_statement', 'company_purchase_statement_result'})

    def test_sale_is_minted_by_admitted_market_view_and_receipt_is_exact(self):
        from authority.client_seat_overlay import company_purchase_config

        with self.assertRaises(ValueError):
            CompanyPurchaseSource(sales=True)
        source = CompanyPurchaseSource(expected_epoch='a' * 32, trade_view=True, sales=True)
        self.addCleanup(source.close)
        registry = {'epoch': 'a' * 32}
        settings = company_purchase_config(source, 7354, registry)
        self.assertIn('sales=true', settings)
        self.client.connect(source.socket.getsockname())
        source.bind_validator(lambda peer: self.peer_valid and peer == self.client.getsockname())
        lease = source._lease
        prefix = f'T|{lease}|'

        def send(payload):
            self.client.send((prefix + payload).encode('ascii'))

        def offer(serial):
            view = f'{lease[:24]}{serial:08x}'
            send(f'MARKET|{view}|sector|CTD-093|0')
            request = source.market_exchange(None)
            self.assertIsNotNone(request)
            source.market_exchange(dict(request, outcome='observed', company_balance_cents=930100,
                total_offers=1, offers=[dict(side='sell', ware='water', price_cents=3473, amount=60,
                    min_amount=2, cargo_units=12, free_units=0, tradeable=True)]))
            header = self.receive()
            self.assertEqual(header[:7], ['MARKET_PAGE', view, '0', 'observed', '930100', '1', '1'])
            sale_id = header[7]
            self.assertRegex(sale_id, r'^[0-9a-f]{32}$')
            self.assertEqual(self.receive()[4:8], ['sell', 'water', '3473', '60'])
            return view, sale_id

        view, sale_id = offer(1)
        packet = f'SALE|{sale_id}|{view}|sector|CTD-093|water|2|3473|WRN-302'
        send(packet.replace('|water|2|', '|water|13|'))
        send(packet.replace('|3473|', '|3474|'))
        self.assertIsNone(source.sale_exchange(None))
        self.peer_valid = False
        send(packet)
        self.assertIsNone(source.sale_exchange(None))
        self.peer_valid = True
        # Closing the view can arrive before a retransmission of its confirmed order.
        send(f'MARKET_CLOSE|{view}')
        send(packet)
        expected = dict(sale_id=sale_id, sector='sector', station_code='CTD-093',
                        ware='water', quantity=2, price_cents=3473)
        self.assertEqual(source.sale_exchange(None), expected)
        send(packet)
        self.assertEqual(source.sale_exchange(None), expected)
        receipt = dict(expected, outcome='confirmed', credited_cents=6900, company_balance_cents=937000,
                       cargo_units=0, ship_code='WRN-302')
        for incompatible in (dict(quantity=3), dict(ship_code='BAD-001'), dict(cargo_units=-1)):
            self.assertEqual(source.sale_exchange({**receipt, **incompatible}), expected)
        self.assertIsNone(source.sale_exchange(receipt))
        answer = ['SALE_RECEIPT', sale_id, 'WRN-302', 'confirmed', '6900', '937000', '0']
        self.assertEqual(self.receive(), answer)
        send(packet)
        self.assertIsNone(source.sale_exchange(None))
        self.assertEqual(self.receive(), answer)

        view, new_id = offer(2)
        self.assertNotEqual(sale_id, new_id)
        packet = f'SALE|{new_id}|{view}|sector|CTD-093|water|2|3473|WRN-302'
        send(packet)
        expected['sale_id'] = new_id
        self.assertEqual(source.sale_exchange(None), expected)
        refusal = dict(expected, outcome='refused', reason='price_changed', current_price_cents=3501)
        self.assertIsNone(source.sale_exchange(refusal))
        self.assertEqual(self.receive(), ['SALE_RECEIPT', new_id, 'WRN-302', 'refused', 'price_changed', '3501'])

        # A pilot can confirm just before a join pauses X4, leaving the first
        # local sale packet to reach the launcher only after its quote expires.
        with patch('authority.company_purchase_source.time.monotonic', return_value=100):
            expired_view, expired_id = offer(3)
        expired_packet = f'SALE|{expired_id}|{expired_view}|sector|CTD-093|water|2|3473|WRN-302'
        with patch('authority.company_purchase_source.time.monotonic', return_value=190):
            for _ in range(2):
                send(expired_packet)
                self.assertIsNone(source.sale_exchange(None))
                self.assertEqual(self.receive(), ['SALE_RECEIPT', expired_id, 'WRN-302', 'refused'])

        next_view, next_id = offer(4)
        send(f'SALE|{next_id}|{next_view}|sector|CTD-093|water|2|3473|WRN-302')
        self.assertEqual(source.sale_exchange(None), {**expected, 'sale_id': next_id})

    def test_native_queue_requires_admission_and_matches_one_receipt(self):
        root = Path(__file__).resolve().parents[1]
        with chdir(root):
            run_fixture(root / 'tests/authority_company_purchase_source.lua')
