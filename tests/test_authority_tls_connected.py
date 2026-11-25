"""One-shot loopback TLS diagnostics use the real tunnel protocol."""
import asyncio
import hashlib
from pathlib import Path
import socket
import ssl
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from authority.experimental_tls_tunnel import create_server_context, relay_connected_client, relay_connected_server
from authority_socket_pair import non_ipv4_connected_pair
from test_authority_tls_tunnel_integration import TEST_CERTIFICATE, TEST_PRIVATE_KEY


def connected_pair():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    connector = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        connector.connect(listener.getsockname())
        accepted, address = listener.accept()
        if address != connector.getsockname() or accepted.getsockname() != connector.getpeername():
            accepted.close()
            raise RuntimeError('unexpected paired connection')
        return connector, accepted
    except BaseException:
        connector.close()
        raise
    finally:
        listener.close()


class ConnectedTunnelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        cert = Path(self.temp.name) / 'cert.pem'
        key = Path(self.temp.name) / 'key.pem'
        cert.write_text(TEST_CERTIFICATE)
        key.write_text(TEST_PRIVATE_KEY)
        self.context = create_server_context(cert, key)
        self.fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(TEST_CERTIFICATE)).hexdigest()

    async def run_pair(self, *, fingerprint=None, client_key=b'a' * 32, exchange=False):
        owner, raw = connected_pair()
        tls_client, tls_server = connected_pair()
        backend, host = connected_pair()
        server = asyncio.create_task(relay_connected_server(
            tls_socket=tls_server, backend_socket=backend, ssl_context=self.context,
            shared_key=b'a' * 32, idle_timeout=1))
        client = asyncio.create_task(relay_connected_client(
            raw_socket=raw, tls_socket=tls_client, server_hostname='127.0.0.1',
            server_fingerprint=fingerprint or self.fingerprint, shared_key=client_key, idle_timeout=1))
        owner_reader, owner_writer = await asyncio.open_connection(sock=owner)
        host_reader, host_writer = await asyncio.open_connection(sock=host)
        try:
            if exchange:
                # Both peers must answer heartbeats before either side sends data.
                await asyncio.sleep(1.5)
                owner_writer.write(b'join')
                await owner_writer.drain()
                self.assertEqual(await asyncio.wait_for(host_reader.readexactly(4), 3), b'join')
                host_writer.write(b'admitted')
                await host_writer.drain()
                self.assertEqual(await asyncio.wait_for(owner_reader.readexactly(8), 3), b'admitted')
                owner_writer.write_eof()
                host_writer.write_eof()
                await asyncio.wait_for(asyncio.gather(server, client), 3)
            else:
                owner_writer.write(b'not admitted')
                await owner_writer.drain()
                self.assertEqual(await asyncio.wait_for(host_reader.read(), 3), b'')
                results = await asyncio.wait_for(asyncio.gather(server, client, return_exceptions=True), 3)
                self.assertTrue(any(isinstance(result, (ValueError, OSError, asyncio.IncompleteReadError))
                                    for result in results), results)
        finally:
            for task in (server, client):
                if not task.done():
                    task.cancel()
            await asyncio.gather(server, client, return_exceptions=True)
            owner_writer.close()
            host_writer.close()
            await asyncio.gather(owner_writer.wait_closed(), host_writer.wait_closed(), return_exceptions=True)
        self.assertTrue(all(sock.fileno() == -1 for sock in (raw, tls_client, tls_server, backend)))

    async def test_real_protocol_and_heartbeat(self):
        await self.run_pair(exchange=True)

    async def test_wrong_fingerprint_never_reaches_backend(self):
        await self.run_pair(fingerprint='0' * 64)

    async def test_wrong_key_never_reaches_backend(self):
        await self.run_pair(client_key=b'b' * 32)

    async def test_opposite_ends_of_same_connection_are_rejected(self):
        first, second = connected_pair()
        with self.assertRaises(ValueError):
            await relay_connected_client(raw_socket=first, tls_socket=second,
                server_hostname='127.0.0.1', server_fingerprint=self.fingerprint,
                shared_key=b'a' * 32)
        self.assertEqual(first.fileno(), -1)
        self.assertEqual(second.fileno(), -1)

    async def test_cancellation_closes_owned_sockets(self):
        tls_server, tls_peer = connected_pair()
        backend, host = connected_pair()
        task = asyncio.create_task(relay_connected_server(
            tls_socket=tls_server, backend_socket=backend, ssl_context=self.context,
            shared_key=b'a' * 32))
        try:
            await asyncio.sleep(.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(tls_server.fileno(), -1)
            self.assertEqual(backend.fileno(), -1)
        finally:
            tls_peer.close()
            host.close()

    async def test_repeated_cancellation_still_closes_both_owned_sockets(self):
        for side in ('client', 'server'):
            with self.subTest(side=side):
                first, first_peer = connected_pair()
                second, second_peer = connected_pair()
                active = asyncio.Event()
                cleanup_started = asyncio.Event()
                cleanup_calls = []

                async def blocked(*args, **kwargs):
                    active.set()
                    await asyncio.Event().wait()

                async def close_writer(writer):
                    cleanup_calls.append(writer)
                    if len(cleanup_calls) == 1:
                        cleanup_started.set()
                        await asyncio.Event().wait()

                try:
                    with patch('authority.experimental_tls_tunnel._accepted_stream',
                               AsyncMock(return_value=(object(), object()))), \
                         patch('authority.experimental_tls_tunnel._server_authenticate', blocked), \
                         patch('authority.experimental_tls_tunnel.asyncio.open_connection', blocked), \
                         patch('authority.experimental_tls_tunnel._close_writer', close_writer):
                        if side == 'client':
                            operation = relay_connected_client(raw_socket=first, tls_socket=second,
                                server_hostname='127.0.0.1', server_fingerprint=self.fingerprint,
                                shared_key=b'a' * 32)
                        else:
                            operation = relay_connected_server(tls_socket=first, backend_socket=second,
                                ssl_context=self.context, shared_key=b'a' * 32)
                        task = asyncio.create_task(operation)
                        await asyncio.wait_for(active.wait(), 1)
                        task.cancel()
                        await asyncio.wait_for(cleanup_started.wait(), 1)
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                    self.assertEqual(len(cleanup_calls), 2)
                    self.assertEqual(first.fileno(), -1)
                    self.assertEqual(second.fileno(), -1)
                finally:
                    first.close()
                    first_peer.close()
                    second.close()
                    second_peer.close()

    async def test_invalid_socket_closes_both_owned_sockets(self):
        raw, peer = non_ipv4_connected_pair()
        tls, other = connected_pair()
        try:
            with self.assertRaises(ValueError):
                await relay_connected_client(raw_socket=raw, tls_socket=tls,
                    server_hostname='127.0.0.1', server_fingerprint=self.fingerprint,
                    shared_key=b'a' * 32)
            self.assertEqual(raw.fileno(), -1)
            self.assertEqual(tls.fileno(), -1)
        finally:
            peer.close()
            other.close()
