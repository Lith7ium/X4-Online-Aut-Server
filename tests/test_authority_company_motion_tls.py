"""One pinned TLS port, real motion and control sockets, synthetic cockpits."""
import asyncio
import hashlib
from pathlib import Path
import ssl
import tempfile
import unittest

from authority.company_motion_client import CompanyMotionClient
from authority.company_motion_service import CompanyMotionService
from authority.experimental_tls_tunnel import create_server_context, start_client, start_server
from test_authority_tls_tunnel_integration import TEST_CERTIFICATE, TEST_PRIVATE_KEY, listening_socket

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


class CompanyMotionTLSTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = CompanyMotionService()
        self.addCleanup(self.service.close)
        self.clients = []
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        certificate, key = (Path(temp.name) / name for name in ('cert.pem', 'key.pem'))
        certificate.write_text(TEST_CERTIFICATE, encoding='ascii')
        key.write_text(TEST_PRIVATE_KEY, encoding='ascii')
        self.fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(TEST_CERTIFICATE)).hexdigest()

        control = await asyncio.start_server(self.echo_control, '127.0.0.1', 0)
        self.addAsyncCleanup(control.wait_closed)
        self.addCleanup(control.close)
        unused_file = self.listener()
        public = self.listener()
        self.public_port = public.getsockname()[1]
        gateway = await start_server(listener=public, ssl_context=create_server_context(certificate, key),
            shared_key=KEY, control_port=control.sockets[0].getsockname()[1],
            file_port=unused_file.getsockname()[1], motion_port=self.service.port,
            max_connections=1, max_motion_connections=2, idle_timeout=15)
        self.addAsyncCleanup(gateway.close)

    @staticmethod
    async def echo_control(reader, writer):
        try:
            writer.write(b'control:' + await reader.readline())
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    def listener(self):
        listener = listening_socket()
        self.addCleanup(listener.close)
        return listener

    async def link(self, *, key=KEY, fingerprint=None):
        control, files, motion = (self.listener() for _ in range(3))
        tunnel = await start_client(control_listener=control, file_listener=files, motion_listener=motion,
            server_host='127.0.0.1', server_port=self.public_port,
            server_fingerprint=fingerprint or self.fingerprint, shared_key=key,
            max_connections=1, idle_timeout=15)
        self.addAsyncCleanup(tunnel.close)
        return control.getsockname()[1], motion.getsockname()[1]

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
        await self.drive(lambda: len(lost_b) == 2)
        self.assertEqual(lost_b[-1], (1970, first_grant['generation']))
        with self.assertRaises(ConnectionError):
            await self.drive(a.poll)
        await self.control_roundtrip(control_port, b'after-motion-revocation')
        stale, _, _, _ = self.attach(motion_a, first_grant)
        with self.assertRaises(ConnectionError):
            await self.drive(stale.poll)
        self.assertEqual(self.service.accepted_samples, 6)

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
