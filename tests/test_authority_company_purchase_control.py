"""Exercise private purchase polling on an admitted, owned loopback socket."""
import json
import socket
import threading
import unittest

import test_authority_experimental_tcp_participants as fixtures
from authority.company_purchase_control import (
    ClientPurchaseControl, CompanyPurchasePeerLost, poll_company_purchase, purchase_receipt,
)
from authority.company_ships import CompanyShip, CompanyShipCatalogue
from authority.experimental_tcp_control import offer_request
from authority.experimental_tcp_participant_client import ExperimentalTCPParticipantClient
from authority.trusted_load_messages import decode_bound_offer, encode_load_claim
from test_authority_trusted_marker_load_session import claim_for


PURCHASE = {'purchase_id': 'b' * 32, 'sector': 'cluster_01_sector001_macro',
            'station_code': 'BIM-169', 'ware': 'water', 'quantity': 6, 'price_cents': 5877}
CONFIRMED = dict(PURCHASE, outcome='confirmed', settled_total_cents=35200,
                 company_balance_cents=964800, cargo_units=6, ship_code='WRN-302')


class Controller:
    def resume(self, handles):
        return handles in ((1,), (2,))

    def pause(self, handles):
        return handles in ((1,), (2,))


class CompanyPurchaseTests(unittest.TestCase):
    def test_receipt_schemas(self):
        self.assertEqual(purchase_receipt(CONFIRMED), CONFIRMED)
        changed = dict(PURCHASE, outcome='refused', reason='price_changed', current_price_cents=5848)
        self.assertEqual(purchase_receipt(changed), changed)
        unknown = dict(PURCHASE, outcome='unknown')
        self.assertEqual(purchase_receipt(unknown), unknown)
        for bad in (dict(CONFIRMED, cargo_units=True), dict(CONFIRMED, settled_total_cents=35200.0),
                    dict(CONFIRMED, company_balance_cents=2**53), dict(changed, current_price_cents=5877),
                    dict(unknown, cargo_units=6), dict(unknown, outcome='uncertain')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                purchase_receipt(bad)

    def test_replayed_poll_and_foreign_bindings_do_not_call_client(self):
        seen = []
        owner = ClientPurchaseControl('epoch', 1, 'a' * 32, 7, lambda value: seen.append(value) or PURCHASE)
        command = {'version': 1, 'kind': 'company_purchase_poll', 'session_epoch': 'epoch',
                   'connection_handle': 1, 'attempt_id': 'a' * 32, 'ship_index': 7,
                   'request_id': 1, 'receipt': None}
        for change in ({'attempt_id': 'c' * 32}, {'ship_index': 8}, {'connection_handle': True},
                       {'session_epoch': 'foreign'}, {'request_id': 2}, {'extra': 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                owner.reply(dict(command, **change), lambda: None)
        self.assertEqual(seen, [])
        self.assertEqual(json.loads(owner.reply(command, lambda: None))['purchase'], PURCHASE)
        with self.assertRaises(ValueError):
            owner.reply(command, lambda: None)
        with self.assertRaises(ValueError):
            owner.reply(dict(command, request_id=2, receipt=dict(CONFIRMED, price_cents=5819)), lambda: None)
        self.assertEqual(seen, [None])
        # A faulty outbound queue may not resubmit the purchase ID after its receipt.
        with self.assertRaises(ValueError):
            owner.reply(dict(command, request_id=2, receipt=CONFIRMED), lambda: None)

    def test_admitted_socket_binds_real_reservation_and_delivers_once(self):
        case = fixtures.ParticipantTCPTest('test_adapter_refuses_use_outside_active_host_operation')
        self.addCleanup(case.doCleanups)
        case.setUp()
        session = case.control._session
        session._company_ship_reader = lambda _checkpoint: CompanyShipCatalogue('d' * 32, (
            CompanyShip(7, '[0x123]', PURCHASE['sector'], 'Company ship', 'WRN-302'),
            CompanyShip(8, '[0x124]', PURCHASE['sector'], 'Second company ship', 'WRN-303'),
        ))
        client = case.connect()
        second = case.connect()

        def exchange(client, raw):
            client.sendall(raw + b'\n')
            case.host.tick(.1, defer_world=True)
            case.host.tick(.1, defer_world=True)
            frame = b''
            while not frame.endswith(b'\n'):
                chunk = client.recv(8192)
                self.assertTrue(chunk)
                frame += chunk
            return frame

        case.host.tick()

        def admit(client, ship_index):
            bound = decode_bound_offer(exchange(client, offer_request().rstrip(b'\n')).rstrip(b'\n'))
            offer = bound.offer
            choose = dict(version=1, kind='choose_company_ship', attempt_id=offer.attempt_id, ship_index=ship_index)
            self.assertEqual(json.loads(exchange(client, json.dumps(choose).encode()))['ship']['ship_index'],
                             ship_index)
            self.assertEqual(json.loads(exchange(client, encode_load_claim(claim_for(offer))))['kind'], 'accepted')
            peer = next(peer for peer in case.control._peers.values() if peer['handle'] == offer.connection_handle)
            # The capability layer owns these flags, independently of reservation.
            peer['company_purchase_ship'], peer['company_purchase_epoch'] = ship_index, 'd' * 32
            return offer

        offer, other_offer = admit(client, 7), admit(second, 8)
        guard_calls, callback_receipts, other_receipts, errors = [], [], [], []
        disconnect = threading.Event()

        def callback(receipt):
            callback_receipts.append(receipt)
            if disconnect.is_set():
                client.shutdown(socket.SHUT_RDWR)
            return PURCHASE if len(callback_receipts) == 1 else None

        def start_client(conn, bound, index, callback):
            started, stop = threading.Event(), threading.Event()

            def client_loop():
                try:
                    owner = ExperimentalTCPParticipantClient(
                        conn, Controller(), enabled=True, epoch=bound.world_epoch,
                        handle=bound.connection_handle, timeout=1, company_purchase_exchange=callback,
                        company_purchase_attempt=bound.attempt_id, company_purchase_ship_index=index)
                    started.set()
                    while not stop.is_set():
                        owner.poll(.02)
                except BaseException as error:
                    if conn is not client or not disconnect.is_set():
                        errors.append(error)
                    started.set()

            worker = threading.Thread(target=client_loop)
            worker.start()

            def finish_client():
                stop.set()
                worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])

            self.addCleanup(finish_client)
            self.assertTrue(started.wait(1))

        start_client(client, offer, 7, callback)
        start_client(second, other_offer, 8, other_receipts.append)
        case.host.tick(.1)
        self.assertEqual(case.world.session.state, 'running')
        args = case.participants, offer.connection_handle, offer.attempt_id, 7
        with self.assertRaises(ValueError):
            poll_company_purchase(*args[:2], 'f' * 32, 7)
        with self.assertRaises(ValueError):
            poll_company_purchase(*args[:3], 8)
        with self.assertRaises(RuntimeError):
            poll_company_purchase(*args, guard=lambda: False)
        self.assertEqual(callback_receipts, [])

        def guard():
            guard_calls.append(True)
            return True

        self.assertEqual(poll_company_purchase(*args, guard=guard), PURCHASE)
        with self.assertRaises(ValueError):
            poll_company_purchase(*args)
        with self.assertRaises(ValueError):
            poll_company_purchase(*args, dict(CONFIRMED, ship_code='ABC-123'))
        self.assertIsNone(poll_company_purchase(*args, CONFIRMED, guard=guard))
        with self.assertRaises(ValueError):
            poll_company_purchase(*args, CONFIRMED)
        self.assertEqual(callback_receipts, [None, CONFIRMED])
        self.assertGreater(len(guard_calls), 2)

        disconnect.set()
        with self.assertRaises(CompanyPurchasePeerLost) as error:
            poll_company_purchase(*args)
        self.assertEqual(error.exception.handle, offer.connection_handle)
        self.assertFalse(case.host._failed)
        self.assertFalse(case.participants._failed)
        self.assertEqual(case.world.session.state, 'running')
        self.assertEqual([handle for handle, _ in case.world.session.ready_players], [other_offer.connection_handle])
        self.assertIsNone(poll_company_purchase(case.participants, other_offer.connection_handle,
                                               other_offer.attempt_id, 8))
        self.assertEqual(other_receipts, [None])
