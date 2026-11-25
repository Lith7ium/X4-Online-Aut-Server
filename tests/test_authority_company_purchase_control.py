"""Exercise private purchase polling on an admitted, owned loopback socket."""
import json
import socket
import threading
import unittest

import test_authority_experimental_tcp_participants as fixtures
from authority.company_purchase_control import (
    ClientPurchaseControl, CompanyPurchasePeerLost, poll_company_purchase, poll_company_undock, purchase_receipt,
    deliver_company_purchase_statement_result, poll_company_fleet, poll_company_sale, request_company_purchase_statement,
)
from authority.company_undock_messages import UndockWindow, undock_receipt, undock_request
from authority.company_ships import CompanyShip, CompanyShipCatalogue
from authority.experimental_tcp_control import offer_request
from authority.experimental_tcp_participant_client import ExperimentalTCPParticipantClient
from authority.trusted_load_messages import decode_bound_offer, encode_load_claim
from test_authority_trusted_marker_load_session import claim_for


PURCHASE = {'purchase_id': 'b' * 32, 'sector': 'cluster_01_sector001_macro',
            'station_code': 'BIM-169', 'ware': 'water', 'quantity': 6, 'price_cents': 5877}
CONFIRMED = dict(PURCHASE, outcome='confirmed', settled_total_cents=35200,
                 company_balance_cents=964800, cargo_units=6, ship_code='WRN-302')

PENDING = dict(PURCHASE, outcome='accepted_pending_statement')
REPORTED = dict(PURCHASE, outcome='pilot_reported', settled_total_cents=35200)
STATEMENT = {'purchase_id': PURCHASE['purchase_id'], 'ware': 'water', 'quantity': 6,
             'settled_total_cents': 35200}


DEPARTURE = dict(request_id='0' * 31 + '1', sector=PURCHASE['sector'], station_code='BIM-169',
                 bay_seed='507326418098681806', bay_macro='dockingbay_arg_s_01_lowtech_macro',
                 dockarea_macro='dockarea_arg_m_station_02_lowtech_macro')


class Controller:
    def resume(self, handles):
        return handles in ((1,), (2,))

    def pause(self, handles):
        return handles in ((1,), (2,))


class CompanyPurchaseTests(unittest.TestCase):
    def test_departure_requires_exact_native_seed_and_one_unmodified_request(self):
        for seed in ('-9223372036854775808', '18446744073709551615'):
            self.assertEqual(undock_request(dict(DEPARTURE, bay_seed=seed))['bay_seed'], seed)
        for seed in (-1, '0', '01', '-0', '18446744073709551616', '-9223372036854775809'):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                undock_request(dict(DEPARTURE, bay_seed=seed))

        window = UndockWindow()
        sent = window.next_request(DEPARTURE)
        window.advance(sent)
        sent['bay_seed'] = '1'
        with self.assertRaises(ValueError):
            window.receipt(dict(sent, outcome='confirmed'))
        with self.assertRaises(ValueError):
            window.next_request(DEPARTURE)
        for outcome in (dict(outcome='unknown'), dict(outcome='refused', reason='busy')):
            receipt = dict(DEPARTURE, **outcome)
            self.assertEqual(window.receipt(receipt), receipt)
        for extra in (dict(outcome='confirmed', reason='busy'), dict(outcome='refused'),
                      dict(outcome='undocked'), dict(outcome=True)):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                undock_receipt(dict(DEPARTURE, **extra))

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

    def test_statement_replay_and_foreign_bindings_do_not_call_client(self):
        seen = []
        owner = ClientPurchaseControl('epoch', 1, 'a' * 32, 7, None,
                                      lambda value: seen.append(value) or STATEMENT)
        command = {'version': 1, 'kind': 'company_purchase_statement', 'session_epoch': 'epoch',
                   'connection_handle': 1, 'attempt_id': 'a' * 32, 'ship_index': 7,
                   'request_id': 1, 'purchase_id': PURCHASE['purchase_id'],
                   'statement_version': 1, 'challenge': 'e' * 32}
        for change in ({'ship_index': 8}, {'statement_version': True}, {'statement_version': 2},
                       {'challenge': 'E' * 32}, {'request_id': 2}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                owner.reply(dict(command, **change), lambda: None)
        self.assertEqual(seen, [])
        answer = json.loads(owner.reply(command, lambda: None))
        self.assertEqual(answer, dict(command, kind='company_purchase_statement_result',
                                      statement=STATEMENT))
        for replay in (command, dict(command, request_id=2)):
            with self.assertRaises(ValueError):
                owner.reply(replay, lambda: None)
        self.assertEqual(seen, [PURCHASE['purchase_id']])
        owner.statement = lambda value: dict(STATEMENT, purchase_id='c' * 32)
        with self.assertRaises(ValueError):
            owner.reply(dict(command, request_id=2, challenge='f' * 32), lambda: None)

    def test_new_connection_can_acknowledge_but_does_not_invent_delivery(self):
        callback = lambda receipt: False
        owner = ClientPurchaseControl('epoch', 1, 'a' * 32, 7, None, statement_result=callback)
        command = {'version': 1, 'kind': 'company_purchase_statement_delivery',
                   'session_epoch': 'epoch', 'connection_handle': 1, 'attempt_id': 'a' * 32,
                   'ship_index': 7, 'request_id': 1, 'statement_version': 1, 'receipt': REPORTED}
        with self.assertRaises(ValueError):
            owner.reply(command, lambda: None)
        owner.statement_result = lambda receipt: receipt == REPORTED
        answer = json.loads(owner.reply(command, lambda: None))
        self.assertEqual(answer['purchase_id'], PURCHASE['purchase_id'])
        self.assertEqual(answer['kind'], 'company_purchase_statement_ack')
        with self.assertRaises(ValueError):
            owner.reply(command, lambda: None)

    def test_admitted_socket_binds_real_reservation_and_delivers_once(self):
        case = fixtures.ParticipantTCPTest('test_adapter_refuses_use_outside_active_host_operation')
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.control._company_purchases = True
        case.control._company_undock = True
        case.control._company_trade_view = True
        case.control._company_fleet_orders = True
        case.control._company_sales = True
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
            if ship_index == 7:
                enable = dict(choose, kind='enable_company_purchases', statement_version=1, undock_version=1,
                              trade_view_version=1, fleet_order_version=1, sale_version=1)
                self.assertEqual(json.loads(exchange(client, json.dumps(enable).encode())), dict(
                    enable, kind='company_purchases_enabled', registry_epoch='d' * 32))
            self.assertEqual(json.loads(exchange(client, encode_load_claim(claim_for(offer))))['kind'], 'accepted')
            peer = next(peer for peer in case.control._peers.values() if peer['handle'] == offer.connection_handle)
            if ship_index == 8:
                # Retain the second pilot's legacy purchase-only capability fixture.
                peer['company_purchase_ship'], peer['company_purchase_epoch'] = ship_index, 'd' * 32
            return offer

        offer, other_offer = admit(client, 7), admit(second, 8)
        guard_calls, callback_receipts, other_receipts, errors = [], [], [], []
        disconnect = threading.Event()
        statements, deliveries, departure_receipts = [], [], []

        view = dict(view_id='f' * 32, sector=PURCHASE['sector'], station_code=PURCHASE['station_code'], offset=0)
        market_pages = []
        def trade_view_exchange(page):
            market_pages.append(page)
            return view if page is None else None

        def undock_exchange(receipt):
            departure_receipts.append(receipt)
            return DEPARTURE if len(departure_receipts) == 1 else None

        order = dict(order_id='e' * 32, ship_index=17, ship_code='NPC-001',
                     sector='sector_1', x=1200.125, y=-20, z=0)
        order_receipts = []
        sale = dict(sale_id='5' * 32, sector='cluster_43_sector001_macro', station_code='CTD-093',
                    ware='water', quantity=2, price_cents=3473)
        sale_receipts = []

        def sale_exchange(receipt):
            sale_receipts.append(receipt)
            return sale if len(sale_receipts) == 1 else None

        def fleet_exchange(receipt):
            order_receipts.append(receipt)
            return order if len(order_receipts) == 1 else None

        def statement_result(receipt):
            deliveries.append(receipt)
            return True

        def statement(purchase_id):
            statements.append(purchase_id)
            return STATEMENT if purchase_id == PURCHASE['purchase_id'] else None

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
                        company_purchase_attempt=bound.attempt_id, company_purchase_ship_index=index,
                        company_purchase_statement=statement if index == 7 else None,
                        company_purchase_statement_result=statement_result if index == 7 else None,
                        company_fleet_exchange=fleet_exchange if index == 7 else None,
                        company_sale_exchange=sale_exchange if index == 7 else None,
                        company_undock_exchange=undock_exchange if index == 7 else None,
                        company_trade_view_exchange=trade_view_exchange if index == 7 else None)
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

        self.assertEqual(poll_company_purchase(*args, guard=guard, market=True),
                         {'purchase': PURCHASE, 'view': view})
        with self.assertRaises(ValueError):
            poll_company_purchase(*args)
        request_statement = request_company_purchase_statement
        purchase_id = PURCHASE['purchase_id']
        self.assertEqual(request_statement(*args, purchase_id, '1' * 32), STATEMENT)
        with self.assertRaises(ValueError):
            request_statement(*args, purchase_id, '1' * 32)
        self.assertEqual(request_statement(*args, purchase_id, '2' * 32), STATEMENT)
        self.assertIsNone(request_statement(*args, '0' * 32, '3' * 32))
        self.assertEqual(statements, [purchase_id, purchase_id, '0' * 32])
        with self.assertRaises(ValueError):
            request_statement(case.participants, other_offer.connection_handle, other_offer.attempt_id,
                              8, purchase_id, '4' * 32)
        with self.assertRaises(ValueError):
            poll_company_purchase(*args, dict(CONFIRMED, ship_code='ABC-123'))
        page = dict(view, outcome='observed', company_balance_cents=CONFIRMED['company_balance_cents'],
                    offers=[], total_offers=0)
        self.assertEqual(poll_company_purchase(*args, CONFIRMED, guard=guard, market=True, page=page),
                         {'purchase': None, 'view': None})
        self.assertEqual(market_pages, [None, page])
        with self.assertRaises(ValueError):
            poll_company_purchase(case.participants, other_offer.connection_handle, other_offer.attempt_id,
                                  8, market=True)
        with self.assertRaises(ValueError):
            poll_company_purchase(*args, CONFIRMED)
        self.assertEqual(callback_receipts, [None, CONFIRMED])
        self.assertEqual(poll_company_undock(*args, guard=guard), DEPARTURE)
        departure_confirmed = dict(DEPARTURE, outcome='confirmed')
        for changes in ({'bay_seed': '1'}, {'station_code': 'ABC-123'}, {'request_id': 'c' * 32}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                poll_company_undock(*args, dict(departure_confirmed, **changes))
        with self.assertRaises(ValueError):
            poll_company_undock(case.participants, other_offer.connection_handle, other_offer.attempt_id, 8)
        self.assertIsNone(deliver_company_purchase_statement_result(*args, REPORTED))
        self.assertIsNone(poll_company_undock(*args, departure_confirmed))
        with self.assertRaises(ValueError):
            poll_company_undock(*args, departure_confirmed)
        self.assertEqual(departure_receipts, [None, departure_confirmed])
        self.assertEqual(poll_company_fleet(*args, guard=guard), order)
        accepted = dict(order, outcome='accepted')
        with self.assertRaises(ValueError):
            poll_company_fleet(*args, dict(accepted, y=-21))
        with self.assertRaises(ValueError):
            poll_company_fleet(case.participants, other_offer.connection_handle, other_offer.attempt_id, 8)
        self.assertIsNone(poll_company_fleet(*args, accepted))
        with self.assertRaises(ValueError):
            poll_company_fleet(*args, accepted)
        self.assertEqual(order_receipts, [None, accepted])
        self.assertEqual(poll_company_sale(*args, guard=guard), sale)
        credited = dict(sale, outcome='confirmed', ship_code='WRN-302', credited_cents=6900,
                        company_balance_cents=937000, cargo_units=10)
        for change in ({'ship_code': 'WRN-303'}, {'price_cents': 3472}, {'sale_id': '6' * 32}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                poll_company_sale(*args, dict(credited, **change))
        with self.assertRaises(ValueError):
            poll_company_sale(case.participants, other_offer.connection_handle, other_offer.attempt_id, 8)
        self.assertIsNone(poll_company_sale(*args, credited))
        with self.assertRaises(ValueError):
            poll_company_sale(*args, credited)
        self.assertEqual(sale_receipts, [None, credited])
        self.assertEqual(deliveries, [REPORTED])
        with self.assertRaises(ValueError):
            deliver_company_purchase_statement_result(*args, CONFIRMED)
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
