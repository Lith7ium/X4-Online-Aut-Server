"""One pinned TLS port, real motion and control sockets, synthetic cockpits."""
import asyncio
import hashlib
import json
import socket
from pathlib import Path
import ssl
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from authority.checkpoints import CheckpointStore
from authority.company_motion_client import CompanyMotionClient
from authority.company_motion_observer import CompanyMotionObserver, MAX_OBSERVER_DATAGRAM
from authority.company_motion_reconnect import ReconnectingCompanyMotionClient
from authority.company_motion_service import CompanyMotionService
from authority.company_motion_source import CompanyMotionSource
from authority.company_ships import CompanyShip, CompanyShipCatalogue
from authority.experimental_local_host import ExperimentalLocalHost
from authority.experimental_remote_load_client import ExperimentalRemoteLoadClient, SessionFull
from authority.experimental_tcp_control import ExperimentalTCPControl, offer_request
from authority.experimental_tls_tunnel import create_server_context, start_client, start_server
from authority.save_transfer import SaveOffers
from authority.trusted_load_messages import decode_bound_offer, encode_load_claim
from authority.trusted_marker_load_session import TrustedMarkerLoadSession
from authority.world import WorldCoordinator
from authority_recovered_tls_gateway import COMPANY_RELIABLE_CONNECTIONS
from lockstep.launcher.joinfiles import FileService
from test_authority_tls_tunnel_integration import TEST_CERTIFICATE, TEST_PRIVATE_KEY, listening_socket
from test_authority_trusted_marker_load_session import FakeEngine, FakeService, claim_for

REGISTRY_EPOCH = 'b' * 32
SESSION_EPOCH = 'a' * 32
KEY = b'm' * 32
VELOCITY = {'x': 16.0, 'y': 3.0, 'z': -2.0}


class SelectedCockpit:
    def __init__(self, index):
        self.registry = REGISTRY_EPOCH, index
        self.local_generation = f'{100 + index:032x}'
        self.sample = None

    def stage(self, sequence, sector, x):
        self.sample = {
            'version': 3, 'epoch': self.registry[0], 'index': self.registry[1],
            'generation': self.local_generation, 'sequence': sequence,
            'sector_macro': sector, 'source_time': sequence / 2, 'capture_time': float(sequence),
            'x': x, 'y': 5.0, 'z': 7.0, 'real_velocity': VELOCITY,
        }

    def poll(self, validate_peer):
        if self.sample is None or not validate_peer(('127.0.0.1', 21000 + self.registry[1])):
            return None
        sample, self.sample = self.sample, None
        return sample


class CompanyMotionTLSHarness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = CompanyMotionService()
        self.addCleanup(self.service.close)
        self.clients = []
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        certificate, key = (Path(temp.name) / name for name in ('cert.pem', 'key.pem'))
        certificate.write_text(TEST_CERTIFICATE, encoding='ascii')
        key.write_text(TEST_PRIVATE_KEY, encoding='ascii')
        self.certificate, self.key = certificate, key
        self.fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(TEST_CERTIFICATE)).hexdigest()

    async def start_gateway(self, control_port, max_connections=1, max_motion_connections=3, *, file_port=None):
        if file_port is None:
            file_port = self.listener().getsockname()[1]
        public = self.listener()
        self.public_port = public.getsockname()[1]
        gateway = await start_server(listener=public, ssl_context=create_server_context(self.certificate, self.key),
            shared_key=KEY, control_port=control_port,
            file_port=file_port, motion_port=self.service.port,
            max_connections=max_connections, max_motion_connections=max_motion_connections, idle_timeout=15)
        self.addAsyncCleanup(gateway.close)

    @staticmethod
    async def echo_control(reader, writer):
        try:
            while request := await reader.readline():
                writer.write(b'control:' + request)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    def listener(self):
        listener = listening_socket()
        self.addCleanup(listener.close)
        return listener

    async def link(self, *, key=KEY, fingerprint=None):
        control, _, motion = await self.link_ports(key=key, fingerprint=fingerprint)
        return control, motion

    async def link_ports(self, *, key=KEY, fingerprint=None, max_connections=1):
        control, files, motion = (self.listener() for _ in range(3))
        tunnel = await start_client(control_listener=control, file_listener=files, motion_listener=motion,
            server_host='127.0.0.1', server_port=self.public_port,
            server_fingerprint=fingerprint or self.fingerprint, shared_key=key,
            max_connections=max_connections, idle_timeout=15)
        self.addAsyncCleanup(tunnel.close)
        return tuple(listener.getsockname()[1] for listener in (control, files, motion))

    def grant(self, handle, index, sector):
        return self.service.admit(handle, f'{handle:032x}', index, sector, SESSION_EPOCH, REGISTRY_EPOCH)

    def attach(self, port, grant):
        source = SelectedCockpit(grant['index'])
        snapshots, ends = [], []
        client = CompanyMotionClient(port, grant, source,
            lambda address: address == ('127.0.0.1', 21000 + grant['index']),
            on_snapshot=snapshots.append, on_end=lambda index, generation: ends.append((index, generation)))
        self.addCleanup(client.close)
        return client, source, snapshots, ends

    async def drive(self, until):
        async with asyncio.timeout(4):
            while not until():
                self.service.poll()
                for client in self.clients:
                    client.poll()
                await asyncio.sleep(.003)

    async def control_roundtrip(self, port, request):
        async with asyncio.timeout(4):
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            try:
                writer.write(request + b'\n')
                await writer.drain()
                self.assertEqual(await reader.readline(), b'control:' + request + b'\n')
            finally:
                writer.close()
                await writer.wait_closed()

class CompanyMotionTLSTests(CompanyMotionTLSHarness):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        control = await asyncio.start_server(self.echo_control, '127.0.0.1', 0)
        self.addAsyncCleanup(control.wait_closed)
        self.addCleanup(control.close)
        await self.start_gateway(control.sockets[0].getsockname()[1])

    async def test_owned_observer_confirmations_cross_real_motion_tunnel(self):
        _, viewer_port = await self.link()
        _, publisher_port = await self.link()
        viewer_grant = self.grant(1, 1970, 'sector_a')
        publisher_grant = self.grant(2, 7354, 'sector_a')
        publisher, cockpit, _, _ = self.attach(publisher_port, publisher_grant)
        viewer_source = CompanyMotionSource(playback_index=7354, cockpit_radar_playback=True)
        self.addCleanup(viewer_source.close)
        config = viewer_source.configure(REGISTRY_EPOCH, viewer_grant['index'], operational_bits='1' * 7354)
        overlay = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(overlay.close)
        overlay.bind(('127.0.0.1', 0))
        overlay.setblocking(False)
        destination = ('127.0.0.1', config['port'])
        prefix = f'C|{config["lease"]}|{config["generation"]}|'.encode('ascii')
        actor, code = '0x20000000000001', 'WRN-302'
        viewer = ReconnectingCompanyMotionClient.observing(
            viewer_port, viewer_grant, viewer_source, lambda address: address == overlay.getsockname(),
        )
        self.addCleanup(viewer.close)
        self.clients = [viewer, publisher]

        def send(message):
            overlay.sendto(prefix + message.encode('ascii'), destination)

        def viewer_peer():
            return next((peer for peer in self.service._peers.values()
                         if peer.grant is not None and peer.grant.handle == 1), None)

        remote = []

        def collect(count):
            while len(remote) < count:
                try:
                    raw, source = overlay.recvfrom(MAX_OBSERVER_DATAGRAM + 1)
                except BlockingIOError:
                    return False
                self.assertEqual(source, destination)
                self.assertTrue(raw.startswith(prefix))
                remote.append(raw[len(prefix):].decode('ascii').split('|'))
            return True

        def seen(row):
            send('|'.join(('SEEN', *row[1:8], actor, code, *row[8:16])))

        send(f'VIEW|cockpit|1|{REGISTRY_EPOCH}|sector_a|7354|{actor}|{code}')
        send(f'VIEW|map|1|{REGISTRY_EPOCH}|sector_a|7354|{actor}|{code}')
        self.service.set_running(True)
        await self.drive(lambda: publisher._running and viewer_peer() is not None and
                         viewer_peer().view_id == 1)
        self.assertEqual(viewer_peer().trial_index, 7354)
        cockpit.stage(1, 'sector_a', 10)
        await self.drive(lambda: collect(2))
        self.assertEqual({row[1] for row in remote}, {'cockpit', 'map'})
        for row in remote:
            self.assertEqual(row[5], publisher_grant['generation'])
            self.assertEqual(row[11:16], ['-', '-', '-', '0.5', '1'])
            if row[1] == 'cockpit':
                self.assertEqual(len(row), 17)
                now = time.perf_counter() if sys.platform == 'win32' else time.monotonic()
                self.assertLess(float(row[16]), now)
            else:
                self.assertEqual(len(row), 16)
        for row in remote:
            seen(row)
        await self.drive(lambda: viewer_source.observer_stats()['samples_confirmed'] == 2)
        self.assertEqual(viewer.stats()['snapshots_received'], 1)

        send(f'VIEW|map|2|{REGISTRY_EPOCH}|-|0|-|-')
        viewer.poll()
        remote.clear()
        cockpit.stage(2, 'sector_a', 26)
        await self.drive(lambda: collect(1))
        self.assertEqual(remote[0][1], 'cockpit')
        self.assertEqual(remote[0][11:16], ['16', '3', '-2', '1', '2'])
        seen(remote[0])
        await self.drive(lambda: viewer_source.observer_stats()['velocity_samples_confirmed'] == 1)

        remote.clear()
        cockpit.stage(3, 'sector_a', 42)
        await self.drive(lambda: collect(1))
        self.service._drop(viewer_peer())
        await self.drive(lambda: viewer._client is None)
        seen(remote[0])
        viewer.poll()
        stats = viewer_source.observer_stats()
        self.assertEqual(stats['samples_confirmed'], 3)
        self.assertEqual(stats['last_confirmed']['sequence'], 2)

    async def test_two_grantees_exchange_visible_motion_and_control_survives_pause_and_revoke(self):
        control_port, motion_a = await self.link()
        _, motion_b = await self.link()
        first_grant, second_grant = self.grant(1, 1970, 'sector_a'), self.grant(2, 7354, 'sector_b')
        a, source_a, seen_a, _ = self.attach(motion_a, first_grant)
        b, source_b, seen_b, lost_b = self.attach(motion_b, second_grant)
        self.clients = [a, b]
        await self.drive(lambda: a._window == b._window == 0)
        a.set_view('sector_b', [1970, 7354])
        b.set_view('sector_b', [1970])  # A starts outside this view.
        self.service.set_running(True)
        await self.drive(lambda: a._running and b._running)
        source_a.stage(1, 'sector_a', 10)
        source_b.stage(1, 'sector_b', 20)
        await self.drive(lambda: a.stats()['samples_acknowledged'] == b.stats()['samples_acknowledged'] == 1
                         and len(seen_a) == 1)
        self.assertEqual([(sample['index'], sample['real_velocity']) for sample in seen_a], [(7354, None)])
        self.assertEqual(seen_b, [])
        await self.control_roundtrip(control_port, b'both-motion-slots-occupied')

        b.set_view('sector_a', [1970])
        await self.drive(lambda: len(seen_b) == 1)
        self.assertEqual((seen_b[0]['index'], seen_b[0]['sector_macro']), (1970, 'sector_a'))
        source_a.stage(2, 'sector_a', 26)
        source_b.stage(2, 'sector_b', 36)
        await self.drive(lambda: len(seen_a) == len(seen_b) == 2 and
                         all(client.stats()['velocity_samples_acknowledged'] == 1 for client in (a, b)))
        self.assertEqual(self.service.samples_with_velocity, 2)
        for sample, grant, x in ((seen_a[-1], second_grant, 36), (seen_b[-1], first_grant, 26)):
            self.assertEqual((sample['index'], sample['generation'], sample['x'], sample['real_velocity']),
                             (grant['index'], grant['generation'], x, VELOCITY))

        source_a.stage(3, 'sector_c', 42)
        await self.drive(lambda: lost_b and a.stats()['samples_acknowledged'] == 3)
        self.assertEqual(lost_b, [(1970, first_grant['generation'])])
        self.assertEqual(len(seen_b), 2)
        b.set_view('sector_c', [1970])
        await self.drive(lambda: len(seen_b) == 3)
        self.assertEqual((seen_b[-1]['sector_macro'], seen_b[-1]['real_velocity']), ('sector_c', None))

        self.service.set_running(False)
        await self.drive(lambda: a._window == b._window == 2)
        source_a.stage(4, 'sector_c', 58)
        await self.drive(lambda: source_a.sample is None)
        await self.control_roundtrip(control_port, b'paused-control')
        self.assertEqual(self.service.accepted_samples, 5)
        self.assertEqual(len(seen_b), 3)
        self.service.set_running(True)
        await self.drive(lambda: a._window == b._window == 3)
        source_a.stage(5, 'sector_c', 74)
        await self.drive(lambda: len(seen_b) == 4)
        self.assertEqual((seen_b[-1]['sequence'], seen_b[-1]['real_velocity']), (5, None))
        self.assertEqual(self.service.accepted_samples, 6)

        self.service.revoke(1)
        self.clients = [b]
        await self.drive(lambda: len(lost_b) == 3)
        self.assertEqual(lost_b[-1], (1970, first_grant['generation']))
        with self.assertRaises(ConnectionError):
            await self.drive(a.poll)
        await self.control_roundtrip(control_port, b'after-motion-revocation')
        stale, _, _, _ = self.attach(motion_a, first_grant)
        with self.assertRaises(ConnectionError):
            await self.drive(stale.poll)
        self.assertEqual(self.service.accepted_samples, 6)

    async def test_motion_only_drop_rebinds_with_same_control_socket_and_sequence_baseline(self):
        control_port, motion_port = await self.link()
        reader, writer = await asyncio.open_connection('127.0.0.1', control_port)

        async def close_control():
            writer.close()
            await writer.wait_closed()
        self.addAsyncCleanup(close_control)

        async def check_control(marker):
            async with asyncio.timeout(3):
                writer.write(marker + b'\n')
                await writer.drain()
                self.assertEqual(await reader.readline(), b'control:' + marker + b'\n')

        source = SelectedCockpit(1970)
        client = ReconnectingCompanyMotionClient(motion_port, self.grant(1, 1970, 'sector_a'),
            source, lambda address: address == ('127.0.0.1', 22970))
        self.addCleanup(client.close)
        self.clients = [client]
        self.service.set_running(True)
        await self.drive(lambda: client._client is not None and client._client._running)
        source.stage(1, 'sector_a', 10)
        await self.drive(lambda: client.stats()['samples_acknowledged'] == 1)
        await check_control(b'before-drop')
        first_link = client._client
        peer, = self.service._peers.values()
        with patch.object(self.service, '_write'):  # Sequence 2 reaches the server, but its ACK cannot reach the client.
            source.stage(2, 'sector_a', 26)
            await self.drive(lambda: self.service.accepted_samples == 2)
        self.assertEqual(client.stats()['samples_acknowledged'], 1)
        self.service._drop(peer)
        await self.drive(lambda: client._client is None)
        source.stage(3, 'sector_a', 42)
        await self.drive(lambda: source.sample is None)
        await check_control(b'during-motion-drop')
        await self.drive(lambda: client._client is not None and client._client._running)
        self.assertIsNot(client._client, first_link)
        self.assertEqual(client._client._last_sequence, 2)
        self.assertEqual(self.service.accepted_samples, 2)
        source.stage(2, 'sector_a', 26)
        await self.drive(lambda: source.sample is None)
        self.assertEqual(self.service.accepted_samples, 2)

        source.stage(4, 'sector_a', 58)
        await self.drive(lambda: client.stats()['samples_acknowledged'] == 2)
        self.assertEqual(client.stats()['velocity_samples_acknowledged'], 0)
        self.assertEqual(self.service._grants[1].last_sequence, 4)
        source.stage(5, 'sector_a', 74)
        await self.drive(lambda: client.stats()['samples_acknowledged'] == 3)
        self.assertEqual(client.stats()['velocity_samples_acknowledged'], 1)
        await check_control(b'after-motion-rebind')

        client.close()
        await self.drive(lambda: not self.service._peers)
        self.assertEqual(client.stats()['samples_acknowledged'], 3)
        self.assertIsNone(client._credential)
        await check_control(b'after-motion-close')

    async def test_reconnecting_view_retires_visibility_and_accepts_only_fresh_samples(self):
        _, viewer_port = await self.link()
        _, source_port = await self.link()
        viewer_grant = self.grant(1, 1970, 'sector_a')
        source_grant = self.grant(2, 7354, 'sector_a')
        source_client, source, _, _ = self.attach(source_port, source_grant)
        seen, ended = [], []
        viewer = ReconnectingCompanyMotionClient(
            viewer_port, viewer_grant, SelectedCockpit(1970), lambda _: True,
            on_snapshot=seen.append, on_end=lambda index, generation: ended.append((index, generation)),
        )
        self.addCleanup(viewer.close)
        self.clients = [viewer, source_client]

        viewer.set_view('sector_a', [1970, 7354])
        await self.drive(lambda: viewer._client is not None and viewer._client._window == 0)

        def viewer_peer():
            return next((peer for peer in self.service._peers.values()
                         if peer.grant is not None and peer.grant.handle == 1), None)

        self.assertEqual(viewer_peer().view_id, 0)
        self.service.set_running(True)
        await self.drive(lambda: source_client._running and viewer_peer().views == {'sector_a': {7354}})
        self.assertEqual(viewer_peer().view_id, 1)
        source.stage(1, 'sector_a', 10)
        await self.drive(lambda: len(seen) == 1)
        source.stage(2, 'sector_a', 26)
        await self.drive(lambda: len(seen) == 2)
        self.assertEqual(seen[-1]['real_velocity'], VELOCITY)

        first_link = viewer._client
        self.service._drop(viewer_peer())
        await self.drive(lambda: viewer._client is None)
        identity = (7354, source_grant['generation'])
        self.assertEqual(ended, [identity])
        viewer.set_view('sector_a', [7354, 999])
        # Server really sends its cached sample after rebinding. The callback
        # must only see it once although the wire records another receipt.
        await self.drive(lambda: viewer.stats()['snapshots_received'] == 3)
        self.assertIsNot(viewer._client, first_link)
        self.assertEqual((viewer_peer().view_id, viewer_peer().views), (1, {'sector_a': {7354, 999}}))
        self.assertEqual([row['sequence'] for row in seen], [1, 2])
        self.assertEqual(ended, [identity])
        source.stage(3, 'sector_a', 42)
        await self.drive(lambda: len(seen) == 3)
        self.assertEqual((seen[-1]['sequence'], seen[-1]['real_velocity']), (3, VELOCITY))

        viewer.set_view('sector_b', [7354])
        viewer.set_view('sector_b', [7354, 1970])  # Same effective view.
        self.assertEqual(ended, [identity, identity])
        await self.drive(lambda: viewer_peer().view_id == 2)
        source.stage(4, 'sector_b', 58)
        await self.drive(lambda: len(seen) == 4)
        self.assertEqual(seen[-1]['sector_macro'], 'sector_b')

        self.service.set_running(False)
        await self.drive(lambda: viewer._client._window == 2 and source_client._window == 2)
        self.assertEqual(viewer_peer().view_id, 0)
        self.assertEqual(ended, [identity] * 3)
        self.service.set_running(True)
        await self.drive(lambda: source_client._window == 3 and viewer_peer().view_id == 1)
        source.stage(5, 'sector_b', 74)
        await self.drive(lambda: len(seen) == 5)
        source.stage(6, 'sector_a', 90)
        await self.drive(lambda: len(ended) == 4)
        self.assertEqual(len(seen), 5)
        source.stage(7, 'sector_b', 106)
        await self.drive(lambda: len(seen) == 6)
        self.assertEqual(seen[-1]['sequence'], 7)
        viewer.close()
        viewer.close()
        self.assertEqual(ended, [identity] * 5)

    async def test_dual_view_replaces_map_and_preserves_cockpit_over_tls(self):
        control, viewer_port = await self.link()
        _, cockpit_port = await self.link()
        _, map_port = await self.link()
        viewer_grant = self.grant(1, 101, 'sector_a')
        cockpit_grant = self.grant(2, 1970, 'sector_a')
        map_grant = self.grant(3, 7354, 'sector_b')
        cockpit, source_a, _, _ = self.attach(cockpit_port, cockpit_grant)
        map_client, source_b, _, _ = self.attach(map_port, map_grant)
        seen, ended = [], []
        viewer = ReconnectingCompanyMotionClient(
            viewer_port, viewer_grant, SelectedCockpit(101), lambda _: True,
            on_snapshot=seen.append, on_end=lambda index, generation: ended.append((index, generation)),
        )
        self.addCleanup(viewer.close)
        self.clients = [viewer, cockpit, map_client]

        def row(sector, *indices):
            return {'sector_macro': sector, 'indices': list(indices)}

        def viewer_peer():
            return next((peer for peer in self.service._peers.values()
                         if peer.grant is not None and peer.grant.handle == 1), None)

        view_ab = [row('sector_a', 101, 1970), row('sector_a', 1970), row('sector_b', 7354)]
        view_ac = [row('sector_a', 1970), row('sector_c', 7354)]
        viewer.set_views(view_ab)
        with self.assertRaises(ValueError):
            viewer.set_views(view_ab + [row('sector_c', 999)])
        with self.assertRaises(ValueError):
            viewer.set_views([row('sector_a', *range(1000, 1256)), row('sector_b', 7354)])
        self.service.set_running(True)
        await self.drive(lambda: cockpit._running and map_client._running and viewer_peer() is not None
                         and viewer_peer().views == {'sector_a': {1970}, 'sector_b': {7354}})
        await self.control_roundtrip(control, b'dual-subscription')
        source_a.stage(1, 'sector_a', 10)
        source_b.stage(1, 'sector_b', 20)
        await self.drive(lambda: len(seen) == 2)
        self.assertEqual({(r['index'], r['sector_macro'], r['sequence']) for r in seen},
                         {(1970, 'sector_a', 1), (7354, 'sector_b', 1)})
        self.assertEqual(ended, [])

        viewer.set_views(view_ac)
        map_identity = (7354, map_grant['generation'])
        cockpit_identity = (1970, cockpit_grant['generation'])
        self.assertEqual(ended, [map_identity])
        source_a.stage(2, 'sector_a', 26)
        source_b.stage(2, 'sector_b', 36)
        await self.drive(lambda: len(seen) == 3 and map_client.stats()['samples_acknowledged'] == 2
                         and viewer_peer().view_id == 2)
        self.assertEqual((seen[-1]['index'], seen[-1]['sequence'], seen[-1]['real_velocity']),
                         (1970, 2, VELOCITY))
        source_b.stage(3, 'sector_c', 52)
        await self.drive(lambda: len(seen) == 4)
        self.assertEqual((seen[-1]['index'], seen[-1]['sector_macro']), (7354, 'sector_c'))
        self.assertEqual(ended, [map_identity])

        viewer.set_views([])
        await self.drive(lambda: not viewer_peer().views)
        self.assertCountEqual(ended, [map_identity, cockpit_identity, map_identity])
        self.service._drop(viewer_peer())
        await self.drive(lambda: viewer._client is None)
        viewer.set_views(view_ac)
        await self.drive(lambda: viewer.stats()['snapshots_received'] == 6)
        self.assertEqual(len(seen), 4)  # Cached positions cannot reappear after clear/reconnect.
        source_a.stage(3, 'sector_a', 42)
        source_b.stage(4, 'sector_c', 68)
        await self.drive(lambda: len(seen) == 6)
        self.assertEqual({(r['index'], r['sector_macro'], r['sequence']) for r in seen[-2:]},
                         {(1970, 'sector_a', 3), (7354, 'sector_c', 4)})
        self.assertIsNone(viewer.failure)
        await self.control_roundtrip(control, b'after-dual-rebind')

    async def test_publisher_control_eof_notifies_tls_viewer_and_revokes_ticket(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / 'store').mkdir()
        store = CheckpointStore(root / 'store')
        engine = FakeEngine(root)
        world = WorldCoordinator('1' * 32, engine, store, SaveOffers(FakeService(), store))
        catalogue = CompanyShipCatalogue(REGISTRY_EPOCH, (
            CompanyShip(7354, '[0x345]', 'sector_a', 'Scout', 'WRN-302'),
            CompanyShip(1970, '[0x346]', 'sector_a', 'Scout', 'WOO-831'),
        ))
        session = TrustedMarkerLoadSession(world, enabled=True, company_ship_reader=lambda _: catalogue)
        control = ExperimentalTCPControl(session, enabled=True, motion_service=self.service)
        self.addCleanup(control.close)
        upstream = self.listener()
        upstream.setblocking(False)
        await self.start_gateway(upstream.getsockname()[1], max_connections=2)

        async def drive_control(until):
            def poll():
                try:
                    connection, _ = upstream.accept()
                except BlockingIOError:
                    pass
                else:
                    control.adopt(connection)
                control.poll()
                return until()
            await self.drive(poll)

        async def connect():
            control_port, motion_port = await self.link()
            reader, writer = await asyncio.open_connection('127.0.0.1', control_port)
            async def close_connection():
                writer.close()
                await writer.wait_closed()
            self.addAsyncCleanup(close_connection)

            async def exchange(message):
                writer.write(message if message.endswith(b'\n') else message + b'\n')
                await writer.drain()
                response = asyncio.create_task(reader.readline())
                await drive_control(response.done)
                result = response.result()
                self.assertTrue(result.endswith(b'\n'))
                return result.rstrip(b'\n')

            return writer, motion_port, exchange

        async def join(connection, index):
            writer, motion_port, exchange = connection
            offer_reply = await exchange(offer_request())
            self.assertEqual(json.loads(offer_reply)['kind'], 'marked_offer')
            offer = decode_bound_offer(offer_reply).offer
            chosen = await exchange(json.dumps({'version':1, 'kind':'choose_company_ship',
                'attempt_id':offer.attempt_id, 'ship_index':index}).encode())
            self.assertEqual(json.loads(chosen)['kind'], 'company_ship_chosen')
            accepted = json.loads(await exchange(encode_load_claim(claim_for(offer), ship_motion=True)))
            self.assertEqual(accepted['kind'], 'accepted')
            return writer, motion_port, offer.connection_handle, accepted['ship_motion']

        publisher_link, viewer_link = await asyncio.gather(connect(), connect())
        await drive_control(lambda: len(control._peers) == 2)
        world.step()
        world.step()
        publisher_control, publisher_port, publisher_handle, publisher_grant = await join(publisher_link, 7354)
        viewer_control, viewer_port, viewer_handle, viewer_grant = await join(viewer_link, 1970)
        world._participants = Mock()
        world._participants.resume.return_value = True
        with patch.object(engine, 'resume'):
            world.step()
        self.assertEqual(world.session.state, 'running')

        publisher, source, _, _ = self.attach(publisher_port, publisher_grant)
        viewer_source = SelectedCockpit(1970)
        snapshots, notices = [], []
        viewer = ReconnectingCompanyMotionClient(viewer_port, viewer_grant, viewer_source, lambda _: True,
            on_snapshot=snapshots.append, on_end=lambda *route: notices.append(('end', *route)),
            on_transport_lost=lambda *route: notices.append(('lost', *route)),
            trial_index=7354, resume_route=lambda: None)
        self.addCleanup(viewer.close)
        viewer.set_view('sector_a', [7354])
        self.clients = [publisher, viewer]
        self.service.set_running(True)
        await drive_control(lambda: publisher._running and viewer._client is not None and viewer._client._running)
        source.stage(1, 'sector_a', 10)
        await drive_control(lambda: len(snapshots) == 1)
        self.assertEqual(snapshots[0]['generation'], publisher_grant['generation'])

        # Leave the motion tunnel connected; it must be severed by control EOF.
        self.clients.remove(publisher)
        publisher_control.close()
        await publisher_control.wait_closed()
        await drive_control(lambda: bool(notices))
        self.assertEqual(notices, [('lost', 7354, publisher_grant['generation'])])
        self.assertNotIn(publisher_handle, self.service._grants)
        self.assertEqual([handle for handle, _ in world.session.ready_players], [viewer_handle])
        self.assertFalse(viewer_control.is_closing())
        self.assertEqual(world.session.state, 'running')
        self.assertIsNone(world.session.pending)

        async def require_eof(client):
            def poll():
                try:
                    client.poll()
                except ConnectionError:
                    return True
                return False
            await drive_control(poll)

        await require_eof(publisher)
        _, retry_port = await self.link()
        refused, retry_source, _, _ = self.attach(retry_port, publisher_grant)
        retry_source.stage(2, 'sector_a', 20)
        await require_eof(refused)
        self.assertEqual(refused.stats()['samples_acknowledged'], 0)
        self.assertEqual([row['sequence'] for row in snapshots], [1])
        self.assertEqual(notices, [('lost', 7354, publisher_grant['generation'])])

        viewer_source.stage(1, 'sector_a', 100)
        await drive_control(lambda: viewer.stats()['samples_acknowledged'] == 1)
        self.assertEqual(self.service.accepted_samples, 2)

    async def test_trial_publisher_drop_is_soft_until_hard_authority_change(self):
        _, viewer_port = await self.link()
        _, source_port = await self.link()
        viewer_grant = self.grant(1, 1970, 'sector_a')
        source_grant = self.grant(2, 7354, 'sector_a')
        events, snapshots = [], []
        viewer = ReconnectingCompanyMotionClient(
            viewer_port, viewer_grant, SelectedCockpit(1970), lambda _: True,
            on_snapshot=snapshots.append,
            on_end=lambda *identity: events.append(('end', *identity)),
            on_transport_lost=lambda *identity: events.append(('lost', *identity)),
            trial_index=7354, resume_route=lambda: None,
        )
        self.addCleanup(viewer.close)
        viewer.set_view('sector_a', [7354])
        self.service.set_running(True)
        self.clients = [viewer]
        identity = (7354, source_grant['generation'])

        async def connect_source():
            client, source, _, _ = self.attach(source_port, source_grant)
            self.clients.append(client)
            await self.drive(lambda: client._running)
            return client, source

        def drop_source(client):
            peer = next(peer for peer in self.service._peers.values()
                        if peer.grant is not None and peer.grant.handle == 2)
            self.service._drop(peer)
            self.clients.remove(client)
            client.close()

        client, source = await connect_source()
        source.stage(1, 'sector_a', 10)
        await self.drive(lambda: len(snapshots) == 1)
        drop_source(client)
        await self.drive(lambda: events == [('lost', *identity)])
        client, source = await connect_source()
        source.stage(2, 'sector_a', 20)
        await self.drive(lambda: len(snapshots) == 2)
        self.assertEqual(events, [('lost', *identity)])
        drop_source(client)
        await self.drive(lambda: len(events) == 2)

        client, source = await connect_source()
        source.stage(3, 'sector_b', 30)
        await self.drive(lambda: len(events) == 3)
        self.assertEqual(events[-2:], [('lost', *identity), ('end', *identity)])
        self.assertEqual([sample['sequence'] for sample in snapshots], [1, 2])
        viewer.set_view('sector_b', [7354])
        await self.drive(lambda: len(snapshots) == 3)

        drop_source(client)
        await self.drive(lambda: len(events) == 4)
        self.service.revoke(2)
        await self.drive(lambda: len(events) == 5)
        self.assertEqual(events[-2:], [('lost', *identity), ('end', *identity)])
        source_grant = self.grant(2, 7354, 'sector_b')
        self.assertNotEqual(source_grant['generation'], identity[1])
        client, source = await connect_source()
        source.stage(1, 'sector_b', 40)
        await self.drive(lambda: len(snapshots) == 4)
        self.assertEqual(snapshots[-1]['generation'], source_grant['generation'])

    async def test_trial_viewer_reconnect_distinguishes_same_and_changed_world_clock(self):
        _, viewer_port = await self.link()
        _, source_port = await self.link()
        viewer_grant = self.grant(1, 1970, 'sector_a')
        source_grant = self.grant(2, 7354, 'sector_a')
        publisher, source, _, _ = self.attach(source_port, source_grant)
        events, snapshots = [], []
        viewer = ReconnectingCompanyMotionClient(
            viewer_port, viewer_grant, SelectedCockpit(1970), lambda _: True,
            on_snapshot=snapshots.append, on_end=lambda *identity: events.append(('end', *identity)),
            on_transport_lost=lambda *identity: events.append(('lost', *identity)),
            on_world_reset=lambda: events.append(('reset',)), trial_index=7354,
            resume_route=lambda: (7354, source_grant['generation'], 'sector_a') if snapshots else None,
        )
        self.addCleanup(viewer.close)
        self.clients = [viewer, publisher]
        self.service.set_running(True)
        viewer.set_view('sector_a', [7354])
        await self.drive(lambda: publisher._running)
        source.stage(1, 'sector_a', 10)
        await self.drive(lambda: len(snapshots) == 1)
        identity = (7354, source_grant['generation'])

        def viewer_server_peer():
            return next(peer for peer in self.service._peers.values()
                        if peer.grant is not None and peer.grant.handle == 1)

        self.service._drop(viewer_server_peer())
        await self.drive(lambda: viewer._client is None)
        self.assertEqual(events, [('lost', *identity)])
        await self.drive(lambda: viewer.stats()['snapshots_received'] == 2)
        self.assertEqual(len(snapshots), 1)  # Cached frame cannot become fresh after rebind.
        source.stage(2, 'sector_a', 20)
        await self.drive(lambda: len(snapshots) == 2)
        self.assertEqual(events, [('lost', *identity)])
        self.service._drop(viewer_server_peer())
        await self.drive(lambda: viewer._client is None)
        self.service.set_running(False)
        self.service.set_running(True)
        await self.drive(lambda: viewer._client is not None and viewer._client._running)
        self.assertEqual(events, [('lost', *identity), ('lost', *identity), ('reset',), ('end', *identity)])

    async def _trial_hard_end_while_viewer_is_disconnected(self, *, sector_change):
        _, viewer_port = await self.link()
        _, source_port = await self.link()
        viewer_grant = self.grant(1, 1970, 'sector_a')
        source_grant = self.grant(2, 7354, 'sector_a')
        publisher, source, _, _ = self.attach(source_port, source_grant)
        messages = []
        observer = CompanyMotionObserver(
            (REGISTRY_EPOCH, 1970), lambda message: messages.append(message.split('|')) or True,
            trial_index=7354,
        )
        viewer = ReconnectingCompanyMotionClient(
            viewer_port, viewer_grant, SelectedCockpit(1970), lambda _: True,
            on_snapshot=observer.snapshot, on_end=observer.end, on_transport_lost=observer.transport_lost,
            on_world_reset=observer.reset_world, trial_index=7354, resume_route=observer.resume_route,
        )
        self.addCleanup(viewer.close)
        self.clients = [viewer, publisher]
        observer.bind_views(viewer.set_views)
        observer.receive(['VIEW', 'cockpit', '1', REGISTRY_EPOCH, 'sector_a', '7354', '0x123abc', 'WRN-302'])
        self.service.set_running(True)
        await self.drive(lambda: publisher._running)
        source.stage(1, 'sector_a', 10)
        await self.drive(lambda: bool(messages))
        initial, = messages
        self.assertEqual(initial[0], 'REMOTE')
        initial_window = self.service.window
        viewer_peer = next(peer for peer in self.service._peers.values()
                           if peer.grant is not None and peer.grant.handle == 1)
        self.service._drop(viewer_peer)
        await self.drive(lambda: viewer._client is None)
        self.assertEqual(messages, [initial])

        if sector_change:
            source.stage(2, 'sector_b', 20)
            await self.drive(lambda: publisher.stats()['samples_acknowledged'] == 2)
        else:
            self.clients.remove(publisher)
            self.service.revoke(2)

        await self.drive(lambda: len(messages) == 2)
        end = ['END', 'cockpit', '1', REGISTRY_EPOCH, '7354', source_grant['generation'], 'sector_a']
        self.assertEqual(messages, [initial, end])
        self.assertEqual(viewer._client._window, initial_window)
        self.assertIsNone(observer.resume_route())

    async def test_trial_same_window_rebind_hard_ends_a_revoked_source_without_new_sample(self):
        await self._trial_hard_end_while_viewer_is_disconnected(sector_change=False)

    async def test_trial_same_window_rebind_hard_ends_a_source_that_changed_sector(self):
        await self._trial_hard_end_while_viewer_is_disconnected(sector_change=True)

    async def test_trial_refuses_a_bind_without_loss_capability(self):
        _, viewer_port = await self.link()
        viewer_grant = self.grant(1, 1970, 'sector_a')
        viewer = ReconnectingCompanyMotionClient(
            viewer_port, viewer_grant, SelectedCockpit(1970), lambda _: True,
            on_snapshot=lambda _: None, on_end=lambda *_: None, on_transport_lost=lambda *_: None,
            trial_index=7354, resume_route=lambda: None,
        )
        self.addCleanup(viewer.close)
        self.clients = [viewer]
        self.service.set_running(True)
        bind = self.service._bind

        def bind_legacy(peer, message):
            bind(peer, {key: value for key, value in message.items() if key not in {'loss_notifications', 'trial_index'}})

        with patch.object(self.service, '_bind', side_effect=bind_legacy):
            await self.drive(lambda: viewer.failure is not None)
        self.assertEqual(viewer.failure, 'motion_local_or_protocol_failure')
        self.assertIsNone(viewer._client)

    async def test_trial_legacy_eof_across_sockets_exhausts_one_initial_bind_budget(self):
        _, viewer_port = await self.link()
        viewer_grant = self.grant(1, 1970, 'sector_a')
        attempts = []

        def legacy_disconnect(peer, message):
            self.assertIs(message.get('loss_notifications'), True)
            attempts.append(peer)
            self.service._drop(peer)

        with patch('authority.company_motion_reconnect.BIND_TIMEOUT', .65), \
                patch.object(self.service, '_bind', side_effect=legacy_disconnect):
            viewer = ReconnectingCompanyMotionClient(
                viewer_port, viewer_grant, SelectedCockpit(1970), lambda _: True,
                on_snapshot=lambda _: None, on_end=lambda *_: None, on_transport_lost=lambda *_: None,
                trial_index=7354, resume_route=lambda: None,
            )
            self.addCleanup(viewer.close)
            self.clients = [viewer]
            await self.drive(lambda: viewer.failure is not None)
            self.assertGreaterEqual(len(attempts), 2)
            self.assertEqual(viewer.failure, 'motion_initial_bind_timeout')
            self.assertIsNone(viewer._client)
            admitted_attempts = len(attempts)
            viewer.poll()
            self.service.poll()
            self.assertEqual(len(attempts), admitted_attempts)

    async def test_legacy_server_can_reject_dual_view_without_dropping_existing_control(self):
        control, motion = await self.link()
        reader, writer = await asyncio.open_connection('127.0.0.1', control)
        writer.write(b'before\n')
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readline(), 2), b'control:before\n')
        self.addCleanup(writer.close)
        source = SelectedCockpit(1970)
        viewer = ReconnectingCompanyMotionClient(motion, self.grant(1, 1970, 'sector_a'),
            source, lambda _: True, on_snapshot=lambda _: None, on_end=lambda *_: None)
        self.addCleanup(viewer.close)
        self.clients = [viewer]
        self.service.set_running(True)
        await self.drive(lambda: viewer._client is not None and viewer._client._running)
        original = self.service._receive

        def legacy_receive(peer, message):
            if message['kind'] == 'motion_views':
                raise ValueError('unknown movement message')
            original(peer, message)

        with patch.object(self.service, '_receive', side_effect=legacy_receive):
            viewer.set_views([{'sector_macro': 'sector_a', 'indices': [7354]},
                              {'sector_macro': 'sector_b', 'indices': [7355]}])
            await self.drive(lambda: viewer._client is None)
            writer.write(b'during\n')
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readline(), 2), b'control:during\n')
            viewer.set_view('sector_a', [7354])
            await self.drive(lambda: any(peer.views == {'sector_a': {7354}}
                                         for peer in self.service._peers.values()))
            writer.write(b'after\n')
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readline(), 2), b'control:after\n')
        writer.close()
        await writer.wait_closed()

    async def test_motion_requires_both_pin_and_shared_key_before_binding(self):
        grant = self.grant(1, 1970, 'sector_a')
        for options in ({'fingerprint': '0' * 64}, {'key': b'x' * 32}):
            with self.subTest(options=tuple(options)):
                _, port = await self.link(**options)
                refused, _, _, _ = self.attach(port, grant)
                with self.assertRaises(ConnectionError):
                    await self.drive(refused.poll)
                self.assertIsNone(refused._window)
        _, port = await self.link()
        accepted, _, _, _ = self.attach(port, grant)
        self.clients = [accepted]
        await self.drive(lambda: accepted._window == 0)


class FourCompanyPilotTLSTest(CompanyMotionTLSHarness):
    async def test_four_pilots_reserve_fly_and_reuse_slot_after_departure(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / 'store').mkdir()
        store = CheckpointStore(root / 'store')
        engine = FakeEngine(root)
        files = FileService('c' * 32, slots=(1,)).start()
        self.addCleanup(files.close)
        world = WorldCoordinator(SESSION_EPOCH, engine, store, SaveOffers(files, store))
        sector = 'sector_a'
        indices = {100, 101, 102, 103}
        catalogue = CompanyShipCatalogue(REGISTRY_EPOCH, tuple(
            CompanyShip(index, f'[0x{index:x}]', sector, 'Fighter', f'FTR-{index}')
            for index in sorted(indices)
        ))
        session = TrustedMarkerLoadSession(world, enabled=True, require_company_ship=True,
                                           company_ship_reader=lambda _: catalogue)
        control = ExperimentalTCPControl(session, enabled=True, motion_service=self.service)
        self.addCleanup(control.close)
        host = ExperimentalLocalHost(world, control, files, enabled=True)
        upstream = self.listener()
        upstream.setblocking(False)
        await self.start_gateway(upstream.getsockname()[1], max_connections=COMPANY_RELIABLE_CONNECTIONS,
                                 max_motion_connections=8, file_port=files.port)

        async def drive_control(until):
            def poll():
                try:
                    connection, _ = upstream.accept()
                except BlockingIOError:
                    pass
                else:
                    host.adopt(connection, defer_attach=True)
                host.tick(defer_world=True)
                return until()
            await self.drive(poll)

        async def connect():
            port, file_port, motion_port = await self.link_ports(max_connections=2)
            reader, writer = await asyncio.open_connection('127.0.0.1', port)

            async def close():
                writer.close()
                await writer.wait_closed()

            self.addAsyncCleanup(close)

            async def exchange(message):
                writer.write(message.rstrip(b'\n') + b'\n')
                await writer.drain()
                response = asyncio.create_task(reader.readline())
                await drive_control(response.done)
                reply = response.result()
                self.assertTrue(reply.endswith(b'\n'))
                return json.loads(reply)

            return close, exchange, motion_port, file_port

        async def choose(link, offer, index):
            return await link[1](json.dumps({'version': 1, 'kind': 'choose_company_ship',
                'attempt_id': offer.attempt_id, 'ship_index': index}).encode())

        async def admit(link, offer):
            reply = await link[1](encode_load_claim(claim_for(offer), ship_motion=True))
            self.assertEqual(reply['kind'], 'accepted')
            return reply['ship_motion']

        probe = await connect()
        applicants = await asyncio.gather(*(connect() for _ in range(5)))
        await drive_control(lambda: len(control._peers) == 6)
        self.assertFalse(world.session.players)  # Accepted probe/control sockets are not pilots.
        replies = await asyncio.gather(*(link[1](offer_request()) for link in applicants))
        self.assertCountEqual([row['kind'] for row in replies], ['not_ready'] * 4 + ['session_full'])
        self.assertEqual(len(world.session.players), 4)
        denied, = [link for link, row in zip(applicants, replies) if row['kind'] == 'session_full']
        await denied[0]()
        await probe[0]()
        await drive_control(lambda: len(control._peers) == 4)
        self.assertEqual(len(world.session.players), 4)
        host.tick()
        host.tick()

        winners = [link for link, row in zip(applicants, replies) if row['kind'] == 'not_ready']
        offers = []
        for link in winners:
            reply = await link[1](offer_request())
            self.assertEqual(reply['kind'], 'marked_offer')
            offers.append(decode_bound_offer(json.dumps(reply).encode()).offer)
        seats = {}
        for index, link, offer in zip(sorted(indices), winners, offers):
            if index == 101:
                duplicate = await choose(link, offer, 100)
                self.assertEqual(duplicate['kind'], 'company_ship_unavailable')
            self.assertEqual((await choose(link, offer, index))['kind'], 'company_ship_chosen')
            seats[index] = (link, offer)
        grants = {index: await admit(link, offer) for index, (link, offer) in seats.items()}
        self.assertEqual({row['index'] for row in grants.values()}, indices)
        self.assertEqual(len(world.session.ready_players), 4)
        world._participants = Mock()
        world._participants.resume.return_value = world._participants.pause.return_value = True
        with patch.object(engine, 'resume'):
            host.tick()
        self.assertEqual(world.session.state, 'running')
        world._participants.resume.assert_called_once_with(tuple(offer.connection_handle for offer in
            sorted(offers, key=lambda row: row.connection_handle)))

        racers = {index: self.attach(seats[index][0][2], grant) for index, grant in grants.items()}
        self.clients = [parts[0] for parts in racers.values()]
        for client in self.clients:
            client.set_view(sector, sorted(indices))  # Own ship is automatically excluded.
        self.service.set_running(True)
        await drive_control(lambda: len(self.service._peers) == 4 and
            all(peer.grant is not None and peer.views == {sector: indices - {peer.grant.index}}
                for peer in self.service._peers.values()))

        async def fly_round(sequence):
            start = {index: len(snapshots) for index, (_, _, snapshots, _) in racers.items()}
            for index, (_, source, _, _) in racers.items():
                source.stage(sequence, sector, index + 16 * sequence)
            await drive_control(lambda: all(client.stats()['samples_acknowledged'] == sequence and
                len(snapshots) == start[index] + len(racers) - 1
                for index, (client, _, snapshots, _) in racers.items()))
            for own_index, (_, _, snapshots, _) in racers.items():
                rows = snapshots[start[own_index]:]
                self.assertCountEqual([row['index'] for row in rows], set(racers) - {own_index})
                for row in rows:
                    self.assertEqual((row['sequence'], row['sector_macro'], row['x'], row['y'], row['z']),
                                     (sequence, sector, row['index'] + 16 * sequence, 5, 7))
                    self.assertEqual(row['generation'], grants[row['index']]['generation'])
                    self.assertEqual(row['real_velocity'], VELOCITY if sequence > 1 else None)

        await fly_round(1)
        await fly_round(2)
        self.assertEqual((self.service.accepted_samples, self.service.samples_with_velocity), (8, 4))
        file_writers = []
        for link in winners:
            _, writer = await asyncio.open_connection('127.0.0.1', link[3])
            self.addCleanup(writer.close)
            file_writers.append(writer)

        def all_file_sockets_open():
            with files.lock:
                return len(files._handlers) == 4

        await drive_control(all_file_sockets_open)
        probe = await connect()
        await drive_control(lambda: len(control._peers) == 5)
        rejected_port, rejected_file_port, _ = await self.link_ports()

        def request_fifth_offer():
            with socket.create_connection(('127.0.0.1', rejected_port), timeout=2) as conn:
                client = ExperimentalRemoteLoadClient(conn, enabled=True, file_host='127.0.0.1',
                    file_port=rejected_file_port, file_token=files.token, file_slot=1,
                    deadline=time.monotonic() + 2)
                return client.request_offer()

        fifth = asyncio.create_task(asyncio.to_thread(request_fifth_offer))
        await drive_control(fifth.done)
        with self.assertRaisesRegex(SessionFull, '^Session Full$'):
            await fifth
        self.assertEqual(world.session.state, 'running')
        self.assertIsNone(world.session.pending)
        await probe[0]()
        for writer in file_writers:
            writer.close()
            await writer.wait_closed()
        await drive_control(lambda: len(control._peers) == 4)

        departing = 100
        departed_client, _, _, _ = racers.pop(departing)
        self.clients.remove(departed_client)
        departed_link, departed_offer = seats[departing]
        await departed_link[0]()  # Control EOF revokes motion without closing surviving clients.
        retired = (departing, grants[departing]['generation'])
        await drive_control(lambda: departed_offer.connection_handle not in self.service._grants
            and all(retired in ends for _, _, _, ends in racers.values()))
        self.assertEqual((world.session.state, len(world.session.ready_players)), ('running', 3))
        self.assertIsNone(world.session.pending)
        await fly_round(3)
        self.assertEqual((self.service.accepted_samples, self.service.samples_with_velocity), (11, 7))

        replacement = await connect()
        self.assertEqual(await replacement[1](offer_request()), {'version': 1, 'kind': 'not_ready'})
        self.assertEqual(world.session.pending.operation, 'pause')
        host.tick()
        host.tick()
        reply = await replacement[1](offer_request())
        replacement_offer = decode_bound_offer(json.dumps(reply).encode()).offer
        self.assertEqual((await choose(replacement, replacement_offer, departing))['kind'],
                         'company_ship_chosen')
        replacement_grant = await admit(replacement, replacement_offer)
        self.assertEqual(replacement_grant['index'], departing)
        self.assertNotEqual(replacement_grant['generation'], grants[departing]['generation'])
        self.assertEqual(len(world.session.ready_players), 4)
