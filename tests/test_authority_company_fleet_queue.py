"""Two admitted TCP pilots command the same Company ship using a synthetic engine."""

from collections import deque
import json
import threading
import unittest

from authority.company_fleet_orders import fleet_order, fleet_receipt
from authority.company_purchase_ledger import CompanyPurchaseLedger
from authority.company_purchase_scheduler import CompanyPurchaseScheduler
from authority.company_ships import CompanyShip, CompanyShipCatalogue
from authority.experimental_tcp_control import offer_request
from authority.experimental_tcp_participant_client import ExperimentalTCPParticipantClient
from authority.trusted_load_messages import decode_bound_offer, encode_load_claim
from test_authority_company_purchase_control import Controller
from test_authority_experimental_tcp_participants import ParticipantTCPFixture
from test_authority_trusted_marker_load_session import claim_for


class SharedFleetQueue(ParticipantTCPFixture):
    def exchange(self, client, frame):
        client.sendall(frame)
        self.host.tick(.1, defer_world=True)
        self.host.tick(.1, defer_world=True)
        data = bytearray()
        while not data.endswith(b'\n'):
            chunk = client.recv(4096)
            self.assertTrue(chunk)
            data.extend(chunk)
        return bytes(data[:-1])

    def admit_pilot(self, client, seat, orders, received):
        offer = decode_bound_offer(self.exchange(client, offer_request())).offer

        def command(kind, **fields):
            message = dict(version=1, kind=kind, attempt_id=offer.attempt_id, **fields)
            return json.loads(self.exchange(client, json.dumps(message).encode() + b'\n'))

        self.assertEqual(command('choose_company_ship', ship_index=seat)['ship']['ship_index'], seat)
        self.assertEqual(command('enable_company_purchases', ship_index=seat, statement_version=1,
                                 fleet_order_version=2)['fleet_order_version'], 2)
        self.assertEqual(json.loads(self.exchange(client, encode_load_claim(claim_for(offer)) + b'\n'))['kind'],
                         'accepted')
        stopped = threading.Event()
        errors = []

        def exchange_fleet(receipt):
            if receipt is not None:
                received.append(receipt)
            return orders.popleft() if orders else None

        def serve():
            try:
                pilot = ExperimentalTCPParticipantClient(
                    client, Controller(), enabled=True, epoch=offer.world_epoch,
                    handle=offer.connection_handle, company_purchase_attempt=offer.attempt_id,
                    company_purchase_ship_index=seat, company_purchase_exchange=lambda _: None,
                    company_fleet_version=2, company_fleet_exchange=exchange_fleet)
                while not stopped.is_set():
                    pilot.poll(.02)
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=serve)
        thread.start()

        def stop():
            stopped.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertFalse(errors, errors)
        self.addCleanup(stop)

    def test_other_pilots_replace_waits_for_first_native_result(self):
        epoch = 'd' * 32
        self.control._company_purchases = self.control._company_fleet_orders = True
        catalogue = tuple(CompanyShip(i, f'[0x{i}]', 'sector', None, f'NPC-00{i}') for i in (1, 2, 3))
        self.control._session._company_ship_reader = lambda _: CompanyShipCatalogue(epoch, catalogue)
        first_pilot, second_pilot = self.connect(), self.connect()
        self.host.tick()
        first = dict(order_id='a' * 32, ship_index=3, ship_code='NPC-003', sector='sector',
                     x=10000, y=0, z=0, queue_mode='append')
        second = dict(first, order_id='b' * 32, x=-20000, queue_mode='replace')
        pending_a, pending_b = deque([first]), deque([second])
        receipts_a, receipts_b, dispatches = [], [], []
        self.admit_pilot(first_pilot, 1, pending_a, receipts_a)
        self.admit_pilot(second_pilot, 2, pending_b, receipts_b)
        self.host.tick(.1)
        self.assertEqual(self.world.session.state, 'running')

        class Fleet:
            active = None

            def begin(self, binding, order):
                if self.active is not None:
                    raise AssertionError('overlapping native orders')
                self.active = order
                dispatches.append(order)

            def poll(self):
                # Both pilots have submitted before the engine accepts the first order.
                if pending_b:
                    return None
                reply = dict(self.active, outcome='accepted')
                self.active = None
                return reply

        ledger = CompanyPurchaseLedger(epoch)
        self.addCleanup(ledger.close)
        now = 0.0
        scheduler = CompanyPurchaseScheduler(self.participants, None, ledger, clock=lambda: now, fleet=Fleet())
        for tick in range(20):
            now = tick / 2
            scheduler.step()
            if receipts_b and receipts_a:
                break
        self.assertEqual(dispatches, [first, second])
        self.assertEqual(receipts_a, [dict(first, outcome='accepted')])
        self.assertEqual(receipts_b, [dict(second, outcome='accepted')])


class FleetOrderVersionTests(unittest.TestCase):
    def test_legacy_and_current_receipts_require_the_negotiated_fields(self):
        legacy = dict(order_id='a' * 32, ship_index=7, ship_code='NPC-003', sector='sector', x=0, y=0, z=0)
        for mode in ('append', 'replace'):
            current = dict(legacy, queue_mode=mode)
            self.assertEqual(fleet_order(current, version=2), current)
            for order, version in ((current, 1), (legacy, 2), (dict(current, queue_mode='clear_all'), 2)):
                with self.assertRaises(ValueError):
                    fleet_receipt(dict(order, outcome='accepted'), version=version)
