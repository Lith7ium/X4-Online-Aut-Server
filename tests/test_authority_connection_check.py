"""The operator CLI uses real TLS and sends no application commands."""
import asyncio
import hashlib
import json
from pathlib import Path
import socket
import ssl
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from authority.experimental_tls_tunnel import create_server_context, start_server
from test_authority_tls_tunnel_integration import TEST_CERTIFICATE, TEST_PRIVATE_KEY, listening_socket


class ConnectionCheck(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        certificate = self.root / 'cert.pem'
        key = self.root / 'key.pem'
        certificate.write_text(TEST_CERTIFICATE, encoding='ascii')
        key.write_text(TEST_PRIVATE_KEY, encoding='ascii')
        self.context = create_server_context(certificate, key)
        self.settings = {
            'server_host': '127.0.0.1',
            'server_fingerprint': hashlib.sha256(ssl.PEM_cert_to_DER_cert(TEST_CERTIFICATE)).hexdigest(),
            'shared_key': 'ab' * 32,
            'file_token': 'cd' * 16,
            'file_slot': 1,
        }

    async def gateway(self, *, file_available=True):
        self.received = asyncio.Queue()

        async def read_backend(reader, writer, name):
            try:
                self.received.put_nowait((name, await reader.read()))
            finally:
                writer.close()
                await writer.wait_closed()

        ports = []
        for name in ('control', 'file'):
            if name == 'file' and not file_available:
                # Reserve a port without listening, so a concurrent test cannot
                # accidentally reuse it and make the backend available.
                unavailable = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.addCleanup(unavailable.close)
                unavailable.bind(('127.0.0.1', 0))
                ports.append(unavailable.getsockname()[1])
                continue
            server = await asyncio.start_server(
                lambda reader, writer, name=name: read_backend(reader, writer, name),
                '127.0.0.1', 0)
            self.addAsyncCleanup(server.wait_closed)
            self.addCleanup(server.close)
            ports.append(server.sockets[0].getsockname()[1])

        listener = listening_socket()
        self.addCleanup(listener.close)
        self.settings['server_port'] = listener.getsockname()[1]
        gateway = await start_server(listener=listener, ssl_context=self.context,
                                     shared_key=bytes.fromhex(self.settings['shared_key']),
                                     control_port=ports[0], file_port=ports[1])
        self.addAsyncCleanup(gateway.close)

    async def check(self, **overrides):
        path = self.root / 'connection.json'
        settings = self.settings | overrides
        path.write_text(json.dumps(settings), encoding='ascii')
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(ROOT / 'tools/dev/authority_connection_check.py'),
            '--connection-file', str(path), stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 8)
        finally:
            if process.returncode is None:
                process.kill()
                await process.communicate()
        self.assertEqual(stderr, b'')
        for field in ('shared_key', 'file_token', 'server_fingerprint'):
            self.assertNotIn(settings[field].encode(), stdout)
        return process.returncode, json.loads(stdout)

    async def test_authenticated_backends_receive_only_eof(self):
        await self.gateway()
        self.assertEqual(await self.check(), (0, {'status': 'ready', 'control': 'ready', 'file': 'ready'}))
        observed = [await asyncio.wait_for(self.received.get(), 2) for _ in range(2)]
        self.assertCountEqual(observed, [('control', b''), ('file', b'')])

    async def test_pin_and_key_fail_before_backend_connection(self):
        await self.gateway()
        for override, expected in (({'server_fingerprint': '0' * 64}, 'certificate_fingerprint_mismatch'),
                                   ({'shared_key': '0' * 64}, 'authentication_or_backend_failed')):
            with self.subTest(expected=expected):
                self.assertEqual(await self.check(**override),
                                 (1, {'status': 'failed', 'control': expected, 'file': expected}))
        self.assertTrue(self.received.empty())

    async def test_missing_backend_is_reported_per_service(self):
        await self.gateway(file_available=False)
        self.assertEqual(await self.check(), (1, {
            'status': 'failed', 'control': 'ready', 'file': 'authentication_or_backend_failed'}))
        self.assertEqual(await asyncio.wait_for(self.received.get(), 2), ('control', b''))
