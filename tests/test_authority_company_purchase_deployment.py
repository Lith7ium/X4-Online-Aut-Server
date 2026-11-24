"""Exercise the player/provider purchase route over real loopback TLS.

Native cockpit confirmation, process ownership, and booking are synthetic.
Admission, offer/transfer, capability negotiation and reconciliation are real.
"""
import hashlib
from pathlib import Path
import socket
import threading
import time
from types import SimpleNamespace
import unittest

from authority.company_purchase_ledger import CompanyPurchaseLedger
from authority.company_purchase_scheduler import CompanyPurchaseScheduler
from authority.company_purchase_source import CompanyPurchaseSource
from authority.company_ships import CompanyShip, CompanyShipCatalogue
from authority.experimental_client_session import client_connection
from authority.experimental_local_host import ExperimentalLocalHost
from authority.experimental_tcp_control import ExperimentalTCPControl
from authority.experimental_tcp_participants import ExperimentalTCPParticipants
from authority.trusted_marker_load_session import TrustedMarkerLoadSession
from authority_client_options import read_connection_settings
from authority_recovered_tls_gateway import RecoveredTLSGateway, gateway_settings
import test_authority_experimental_local_host as host_fixture
from test_authority_tls_tunnel_integration import Controller, TEST_CERTIFICATE, TEST_PRIVATE_KEY


REGISTRY_EPOCH = 'd' * 32
SHIP_INDEX = 7354
SECTOR = 'cluster_01_sector001_macro'
DISPLAYED_TOTAL = 34914


class NativePurchaseWithMissingReceipt:
    """Count destructive calls separately from the lost receipt."""

    def __init__(self):
        self.bookings = []
        self.balance_cents = 1000000
        self.cargo_units = 0

    def begin(self, binding, purchase):
        self.bookings.append((binding, purchase))
        self.balance_cents -= 34900
        self.cargo_units += purchase['quantity']

    def poll(self):
        return None


class CompanyPurchaseDeploymentTests(unittest.TestCase):
    def test_lost_native_receipt_recovers_from_distinct_pilot_challenges_over_tls(self):
        fixture = host_fixture.HostTest(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.control.close()
        root = Path(fixture.temp.name)
        catalogue = CompanyShipCatalogue(REGISTRY_EPOCH, (
            CompanyShip(SHIP_INDEX, '[0x345]', SECTOR, 'Scout', 'WRN-302'),
        ))
        fixture.control = ExperimentalTCPControl(
            TrustedMarkerLoadSession(fixture.world, enabled=True, company_ship_reader=lambda _: catalogue,
                                     require_company_ship=True),
            enabled=True, company_purchases=True)
        host = ExperimentalLocalHost(fixture.world, fixture.control, fixture.service, enabled=True)
        participants = ExperimentalTCPParticipants(host, enabled=True, timeout=3)
        fixture.world._participants = participants
        fixture.listener.setblocking(False)

        certificate, key, shared_key = (root / name for name in ('test-cert.pem', 'test-key.pem', 'test-shared'))
        certificate.write_text(TEST_CERTIFICATE, encoding='ascii')
        key.write_text(TEST_PRIVATE_KEY, encoding='ascii')
        shared_key.write_text('ab' * 32, encoding='ascii')
        options = gateway_settings(SimpleNamespace(
            tls_bind='127.0.0.1', tls_advertise='127.0.0.1', tls_port=0,
            tls_certificate=certificate, tls_private_key=key, tls_shared_key_file=shared_key))
        gateway = RecoveredTLSGateway(options, out=root, control_port=fixture.listener.getsockname()[1],
                                      file_port=fixture.service.port, file_token=fixture.service.token)
        self.addCleanup(gateway.close)
        gateway.start(3)
        settings = read_connection_settings(gateway.connection, require_private=True)

        ledger = CompanyPurchaseLedger(REGISTRY_EPOCH, root / 'purchase-ledger.sqlite3')
        self.addCleanup(ledger.close)
        native = NativePurchaseWithMissingReceipt()
        clock = [0.]
        schedule = CompanyPurchaseScheduler(participants, native, ledger, clock=lambda: clock[0],
                                            verify_timeout=1)
        stop, retry_local_datagram = threading.Event(), threading.Event()
        errors, offers, notifications, statements, local_notices = [], [], [], [], []
        controller = Controller()

        def run_player():
            try:
                with client_connection(**settings, deadline=time.monotonic() + 15, io_timeout=3) as owner:
                    bound = owner.request_offer()
                    offers.append(bound)
                    self.assertEqual([ship['ship_index'] for ship in owner.list_company_ships()], [SHIP_INDEX])
                    self.assertEqual(owner.choose_company_ship(SHIP_INDEX)['code'], 'WRN-302')
                    epoch = owner.enable_company_purchases()
                    self.assertEqual(epoch, REGISTRY_EPOCH)
                    downloaded = owner.download(root / 'player-download')
                    slot = owner.copy_to_new_slot(downloaded, root / 'save_009.xml.gz')
                    self.assertEqual(hashlib.sha256(slot.read_bytes()).hexdigest(), bound.offer.sha256)
                    attempt = bound.offer.attempt_id
                    owner.load_requested(attempt)
                    owner.loaded_marker(attempt, bound.marker)
                    owner.paused(attempt, True)

                    source = CompanyPurchaseSource(expected_epoch=epoch,
                        server_fingerprint=settings['server_fingerprint'], state_dir=root / 'player-state')
                    native_client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    try:
                        config = source.configure(epoch, SHIP_INDEX)
                        native_client.connect(('127.0.0.1', config['port']))
                        native_client.setblocking(False)
                        source.bind_validator(lambda peer: peer == native_client.getsockname())
                        callbacks = source.control_callbacks(owner)
                        self.assertEqual(set(callbacks), {'company_purchase_exchange',
                            'company_purchase_statement', 'company_purchase_statement_result'})

                        def record_statement(purchase_id):
                            answer = source.statement(purchase_id)
                            statements.append(answer)
                            return answer

                        def record_result(receipt):
                            if not source.statement_result(receipt):
                                return False
                            notifications.append(receipt)
                            return True

                        callbacks.update(company_purchase_statement=record_statement,
                                         company_purchase_statement_result=record_result)
                        owner.activate(attempt, controller, participant_timeout=3, **callbacks)
                        packet = f"T|{config['lease']}|1|{SECTOR}|BIM-169|water|6|5819|{DISPLAYED_TOTAL}".encode()
                        sent = retried = False
                        while not stop.is_set():
                            owner.poll(.025)
                            if controller.operations and not sent:
                                native_client.send(packet)
                                sent = True
                            if retry_local_datagram.is_set() and not retried:
                                native_client.send(packet)
                                retried = True
                            try:
                                local_notices.append(native_client.recv(512).decode().split('|')[2:])
                            except BlockingIOError:
                                pass
                    finally:
                        native_client.close()
                        source.close()
            except BaseException as error:
                errors.append(error)

        host.tick()
        player = threading.Thread(target=run_player, daemon=True)
        player.start()
        try:
            end = time.monotonic() + 10
            while time.monotonic() < end and not errors:
                try:
                    conn, _ = fixture.listener.accept()
                except BlockingIOError:
                    pass
                else:
                    host.adopt(conn, defer_attach=True)
                host.tick(.025)
                if fixture.world.session.state != 'running' or fixture.world.session.pending is not None:
                    continue
                clock[0] += 2.
                schedule.step()
                if notifications and len(local_notices) == 2:
                    retry_local_datagram.set()
                if len(local_notices) == 3:
                    break
            self.assertFalse(errors, repr(errors))
            self.assertEqual(len(offers), 1)
            peer, = fixture.control._peers.values()
            self.assertEqual((peer['stage'], peer['company_purchase_ship'], peer['company_purchase_epoch'],
                              peer['company_purchase_statement_version']),
                             ('admitted', SHIP_INDEX, REGISTRY_EPOCH, 1))
            self.assertEqual(controller.operations, [('resume', (offers[0].offer.connection_handle,))])
            self.assertEqual(len(native.bookings), 1)
            binding, purchase = native.bookings[0]
            self.assertEqual(binding, dict(registry_epoch=REGISTRY_EPOCH, ship_index=SHIP_INDEX, ship_code='WRN-302'))
            self.assertEqual((native.balance_cents, native.cargo_units), (965100, 6))
            self.assertEqual(notifications, [dict(purchase, outcome='pilot_reported', settled_total_cents=DISPLAYED_TOTAL)])
            confirmed_report = ['RECEIPT', '1', 'pilot_reported', str(DISPLAYED_TOTAL)]
            self.assertEqual(local_notices, [
                ['RECEIPT', '1', 'accepted_pending_statement'], confirmed_report, confirmed_report,
            ])
            statement = dict(purchase_id=purchase['purchase_id'], ware='water', quantity=6,
                             settled_total_cents=DISPLAYED_TOTAL)
            # Two independent convergence requests plus recognition before notification.
            self.assertEqual(statements, [statement] * 3)
            challenges = ledger._db.execute('SELECT challenge FROM challenges WHERE purchase_id=?',
                                            (purchase['purchase_id'],)).fetchall()
            self.assertEqual(len(set(challenges)), 2)
            self.assertIsNone(ledger.next_report())
            self.assertIsNone(ledger.next_notification())
        finally:
            stop.set()
            player.join(8)
            self.assertFalse(player.is_alive())
        self.assertFalse(errors, repr(errors))
