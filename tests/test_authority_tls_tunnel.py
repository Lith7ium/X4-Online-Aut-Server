"""Local TLS transport tests; all keys below are public test fixtures."""
import asyncio
import hashlib
from pathlib import Path
import socket
import ssl
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests'), str(ROOT / 'tools/dev')]

from authority.experimental_client_session import client_connection
from authority.experimental_tls_tunnel import (
    _MAGIC, _MOTION, _client_context, _relay, create_server_context, start_client, start_server,
)
from authority_recovered_tls_gateway import RecoveredTLSGateway
from authority_windows_fixture_client import read_connection_settings
from test_authority_tls_tunnel_integration import TEST_CERTIFICATE, TEST_PRIVATE_KEY


def listener():
    result = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result.bind(('127.0.0.1', 0))
    result.listen(16)
    return result


class TunnelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        certificate = Path(self.temp.name) / 'cert.pem'
        private_key = Path(self.temp.name) / 'key.pem'
        certificate.write_text(TEST_CERTIFICATE)
        private_key.write_text(TEST_PRIVATE_KEY)
        self.context = create_server_context(certificate, private_key)
        self.fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(TEST_CERTIFICATE)).hexdigest()
        self.tunnels = []
        self.servers = []
        self.backend_connections = []
        self.addAsyncCleanup(self.cleanup_network)

    async def cleanup_network(self):
        for tunnel in reversed(self.tunnels):
            await tunnel.close()
        for server in self.servers:
            server.close()
            await server.wait_closed()

    async def endpoint(self, prefix, arrived=None):
        async def handler(reader, writer):
            self.backend_connections.append(prefix)
            if arrived is not None:
                arrived.set()
            try:
                payload = await reader.read()
                writer.write(prefix + payload)
                await writer.drain()
                writer.write_eof()
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(handler, '127.0.0.1', 0)
        self.servers.append(server)
        return server.sockets[0].getsockname()[1]

    async def make_pair(self, *, key=b'a' * 32, fingerprint=None, max_connections=8, idle_timeout=5):
        control_port = await self.endpoint(b'control:')
        file_port = await self.endpoint(b'file:')
        host = listener()
        host_port = host.getsockname()[1]
        server = await start_server(listener=host, ssl_context=self.context,
                                    shared_key=b'a' * 32, control_port=control_port,
                                    file_port=file_port, max_connections=max_connections,
                                    idle_timeout=idle_timeout)
        self.tunnels.append(server)
        control, files = listener(), listener()
        client_ports = control.getsockname()[1], files.getsockname()[1]
        client = await start_client(control_listener=control, file_listener=files,
                                    server_host='127.0.0.1', server_port=host_port,
                                    server_fingerprint=fingerprint or self.fingerprint,
                                    shared_key=key, max_connections=max_connections,
                                    idle_timeout=idle_timeout)
        self.tunnels.append(client)
        return client_ports

    async def motion_server(self, *, motion_enabled=True, arrived=None):
        host = listener()
        host_port = host.getsockname()[1]
        control_port = await self.endpoint(b'control:')
        file_port = await self.endpoint(b'file:')
        motion_port = await self.endpoint(b'motion:', arrived) if motion_enabled else None
        server = await start_server(listener=host, ssl_context=self.context, shared_key=b'a' * 32,
                                    control_port=control_port, file_port=file_port, motion_port=motion_port,
                                    max_connections=2, max_motion_connections=1)
        self.tunnels.append(server)
        return host_port

    async def motion_client(self, host_port, *, fingerprint=None, key=b'a' * 32):
        control, files, motion = listener(), listener(), listener()
        ports = tuple(item.getsockname()[1] for item in (control, files, motion))
        client = await start_client(control_listener=control, file_listener=files, motion_listener=motion,
                                    server_host='127.0.0.1', server_port=host_port,
                                    server_fingerprint=fingerprint or self.fingerprint, shared_key=key,
                                    max_connections=1, max_motion_connections=2)
        self.tunnels.append(client)
        return ports

    async def exchange(self, port, payload):
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        writer.write(payload)
        await writer.drain()
        writer.write_eof()
        response = await asyncio.wait_for(reader.read(), 10)
        writer.close()
        await writer.wait_closed()
        return response

    async def test_large_transfer_and_both_listeners_with_single_slot(self):
        control, files = await self.make_pair(max_connections=1)
        payload = bytes(range(256)) * 2048
        self.assertEqual(await self.exchange(files, payload), b'file:' + payload)
        self.assertEqual(await self.exchange(control, b'command'), b'control:command')

    async def test_motion_capacity_is_separate_on_both_sides(self):
        arrived = asyncio.Event()
        host_port = await self.motion_server(arrived=arrived)
        control, files, motion = await self.motion_client(host_port)
        reader, writer = await asyncio.open_connection('127.0.0.1', motion)
        try:
            await asyncio.wait_for(arrived.wait(), 3)
            overflow, excess = await asyncio.open_connection('127.0.0.1', motion)
            try:
                self.assertEqual(await asyncio.wait_for(overflow.read(), 3), b'')
            finally:
                excess.close()
                await excess.wait_closed()
            self.assertEqual(await self.exchange(control, b'command'), b'control:command')
            self.assertEqual(await self.exchange(files, b'save'), b'file:save')
            writer.write(b'snapshot')
            await writer.drain()
            writer.write_eof()
            self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'motion:snapshot')
            self.assertCountEqual(self.backend_connections, [b'control:', b'file:', b'motion:'])
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_motion_requires_negotiated_service_pin_and_motion_key_domain(self):
        legacy_port = await self.motion_server(motion_enabled=False)
        control, files, motion = await self.motion_client(legacy_port)
        self.assertEqual(await self.exchange(motion, b'unsupported'), b'')
        self.assertEqual(await self.exchange(control, b'legacy'), b'control:legacy')
        self.assertEqual(await self.exchange(files, b'legacy'), b'file:legacy')

        host_port = await self.motion_server()
        for label, credentials in (('wrong pin', {'fingerprint': '0' * 64}),
                                   ('wrong key', {'key': b'b' * 32})):
            with self.subTest(label=label):
                _, _, motion = await self.motion_client(host_port, **credentials)
                self.assertEqual(await self.exchange(motion, b'unauthorized'), b'')

        reader, writer = await asyncio.open_connection('127.0.0.1', host_port,
                                                       ssl=_client_context(), server_hostname='')
        try:
            writer.write(_MAGIC + b'a' * 32 + bytes([_MOTION]))
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'')
        finally:
            writer.close()
            await writer.wait_closed()
        self.assertNotIn(b'motion:', self.backend_connections)

    async def test_foreground_session_exposes_motion_only_during_opt_in_context(self):
        options = ('127.0.0.1', 0, '127.0.0.1', None, self.fingerprint, self.context, b'a' * 32)
        gateway = RecoveredTLSGateway(options, out=Path(self.temp.name),
                                      control_port=await self.endpoint(b'control:'),
                                      file_port=await self.endpoint(b'file:'), file_token='a' * 32,
                                      motion_port=await self.endpoint(b'motion:'))
        self.addAsyncCleanup(asyncio.to_thread, gateway.close)
        await asyncio.to_thread(gateway.start, 3)
        settings = read_connection_settings(gateway.connection)

        def use_session():
            options = dict(settings, deadline=time.monotonic() + 10, io_timeout=3)
            with client_connection(**options) as legacy:
                self.assertIsNone(legacy.motion_port)
            with client_connection(**options, motion_enabled=True) as owner:
                port = owner.motion_port
                self.assertIsInstance(port, int)
                with socket.create_connection(('127.0.0.1', port), timeout=3) as conn:
                    conn.sendall(b'snapshot')
                    conn.shutdown(socket.SHUT_WR)
                    with conn.makefile('rb') as stream:
                        self.assertEqual(stream.read(), b'motion:snapshot')
            with self.assertRaises(ConnectionRefusedError):
                socket.create_connection(('127.0.0.1', port), timeout=1)

        await asyncio.to_thread(use_session)

    async def test_idle_connection_remains_usable(self):
        control, _ = await self.make_pair(idle_timeout=1)
        reader, writer = await asyncio.open_connection('127.0.0.1', control)
        await asyncio.sleep(2.5)
        writer.write(b'after idle')
        await writer.drain()
        writer.write_eof()
        self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'control:after idle')
        writer.close()
        await writer.wait_closed()

    async def test_idle_half_closed_connection_can_still_receive(self):
        async def delayed_backend(request, response):
            data = await request.read()
            await asyncio.sleep(2.5)
            response.write(b'delayed:' + data)
            await response.drain()
            response.write_eof()
            response.close()
            await response.wait_closed()

        backend = await asyncio.start_server(delayed_backend, '127.0.0.1', 0)
        self.servers.append(backend)
        host = listener()
        host_port = host.getsockname()[1]
        backend_port = backend.sockets[0].getsockname()[1]
        server = await start_server(listener=host, ssl_context=self.context, shared_key=b'a' * 32,
                                    control_port=backend_port, file_port=backend_port, idle_timeout=1)
        self.tunnels.append(server)
        control_listener, file_listener = listener(), listener()
        control_port = control_listener.getsockname()[1]
        client = await start_client(control_listener=control_listener, file_listener=file_listener,
                                    server_host='127.0.0.1', server_port=host_port,
                                    server_fingerprint=self.fingerprint, shared_key=b'a' * 32,
                                    idle_timeout=1)
        self.tunnels.append(client)
        reader, writer = await asyncio.open_connection('127.0.0.1', control_port)
        writer.write(b'before half close')
        await writer.drain()
        writer.write_eof()
        self.assertEqual(await asyncio.wait_for(reader.read(), 5), b'delayed:before half close')
        writer.close()
        await writer.wait_closed()

    async def test_unresponsive_peer_is_closed_even_after_ping_is_sent(self):
        ping_received = asyncio.Event()

        async def silent_peer(reader, writer):
            try:
                await reader.readexactly(41)
                writer.write(b'\x01')
                await writer.drain()
                self.assertEqual(await reader.readexactly(5), struct.pack('!BI', 3, 8))
                await reader.readexactly(8)
                ping_received.set()
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()

        peer = await asyncio.start_server(silent_peer, '127.0.0.1', 0, ssl=self.context)
        self.servers.append(peer)
        control, files = listener(), listener()
        port = control.getsockname()[1]
        client = await start_client(control_listener=control, file_listener=files,
                                    server_host='127.0.0.1', server_port=peer.sockets[0].getsockname()[1],
                                    server_fingerprint=self.fingerprint, shared_key=b'a' * 32,
                                    idle_timeout=1)
        self.tunnels.append(client)
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        await asyncio.wait_for(ping_received.wait(), 2)
        self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'')
        writer.close()
        await writer.wait_closed()

    async def test_heartbeat_response_does_not_block_tls_reader_behind_writer(self):
        inbound_data_read = asyncio.Event()
        raw_data_delivered = asyncio.Event()
        allow_exit = asyncio.Event()

        class RawReader:
            async def read(self, size):
                if not hasattr(self, 'sent'):
                    self.sent = True
                    return b'x'
                await allow_exit.wait()
                return b''

        class RawWriter:
            def write(self, data):
                self.data = data
                raw_data_delivered.set()
            async def drain(self):
                return

        class TLSReader:
            def __init__(self):
                self.parts = iter((struct.pack('!BI', 3, 8), b'12345678',
                                   struct.pack('!BI', 1, 1), b'y'))
            async def readexactly(self, size):
                part = next(self.parts, None)
                if part is None:
                    await allow_exit.wait()
                    raise asyncio.IncompleteReadError(b'', size)
                if part == struct.pack('!BI', 1, 1):
                    inbound_data_read.set()
                return part

        class TLSWriter:
            def write(self, data):
                self.kind = data[0]
            async def drain(self):
                if self.kind == 1:
                    await inbound_data_read.wait()

        task = asyncio.create_task(_relay(RawReader(), RawWriter(), TLSReader(), TLSWriter(), 1))
        try:
            await asyncio.wait_for(raw_data_delivered.wait(), .5)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_invalid_heartbeat_response_closes_connection(self):
        pong_sent = asyncio.Event()

        async def wrong_pong(reader, writer):
            try:
                await reader.readexactly(41)
                writer.write(b'\x01')
                await writer.drain()
                self.assertEqual(await reader.readexactly(5), struct.pack('!BI', 3, 8))
                challenge = await reader.readexactly(8)
                wrong_challenge = bytes([challenge[0] ^ 1]) + challenge[1:]
                writer.write(struct.pack('!BI', 4, 8) + wrong_challenge)
                await writer.drain()
                pong_sent.set()
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()

        peer = await asyncio.start_server(wrong_pong, '127.0.0.1', 0, ssl=self.context)
        self.servers.append(peer)
        control, files = listener(), listener()
        port = control.getsockname()[1]
        client = await start_client(control_listener=control, file_listener=files,
                                    server_host='127.0.0.1', server_port=peer.sockets[0].getsockname()[1],
                                    server_fingerprint=self.fingerprint, shared_key=b'a' * 32,
                                    idle_timeout=3)
        self.tunnels.append(client)
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        await asyncio.wait_for(pong_sent.wait(), 3)
        self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')
        writer.close()
        await writer.wait_closed()

    async def test_full_duplex_large_transfer_after_idle(self):
        payload_from_client = b'c' * (4 * 1024 * 1024)
        payload_from_server = b's' * (4 * 1024 * 1024)
        received = asyncio.get_running_loop().create_future()

        async def backend(reader, writer):
            try:
                async def send():
                    await asyncio.sleep(1.3)
                    writer.write(payload_from_server)
                    await writer.drain()
                    writer.write_eof()
                _, client_bytes = await asyncio.gather(send(), reader.read())
                received.set_result(client_bytes)
            finally:
                writer.close()
                await writer.wait_closed()

        endpoint = await asyncio.start_server(backend, '127.0.0.1', 0)
        self.servers.append(endpoint)
        host = listener()
        host_port = host.getsockname()[1]
        endpoint_port = endpoint.sockets[0].getsockname()[1]
        server = await start_server(listener=host, ssl_context=self.context, shared_key=b'a' * 32,
                                    control_port=endpoint_port, file_port=endpoint_port, idle_timeout=1)
        self.tunnels.append(server)
        control, files = listener(), listener()
        port = control.getsockname()[1]
        client = await start_client(control_listener=control, file_listener=files,
                                    server_host='127.0.0.1', server_port=host_port,
                                    server_fingerprint=self.fingerprint, shared_key=b'a' * 32,
                                    idle_timeout=1)
        self.tunnels.append(client)
        reader, writer = await asyncio.open_connection('127.0.0.1', port)

        async def send():
            await asyncio.sleep(1.3)
            writer.write(payload_from_client)
            await writer.drain()
            writer.write_eof()

        _, server_bytes = await asyncio.wait_for(asyncio.gather(send(), reader.read()), 10)
        self.assertEqual(server_bytes, payload_from_server)
        self.assertEqual(await asyncio.wait_for(received, 3), payload_from_client)
        writer.close()
        await writer.wait_closed()

    async def test_wrong_key_never_reaches_backend(self):
        control, _ = await self.make_pair(key=b'b' * 32)
        self.assertEqual(await self.exchange(control, b'command'), b'')
        self.assertEqual(self.backend_connections, [])

    async def test_version_one_client_is_rejected_before_backend_connection(self):
        backend_port = await self.endpoint(b'backend:')
        host = listener()
        host_port = host.getsockname()[1]
        server = await start_server(listener=host, ssl_context=self.context, shared_key=b'a' * 32,
                                    control_port=backend_port, file_port=backend_port)
        self.tunnels.append(server)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        reader, writer = await asyncio.open_connection('127.0.0.1', host_port,
                                                        ssl=context, server_hostname='')
        writer.write(b'X4TUN01\0' + b'a' * 32 + b'\x01')
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'')
        self.assertEqual(self.backend_connections, [])
        writer.close()
        await writer.wait_closed()

    async def test_wrong_fingerprint_sends_no_shared_key(self):
        received = asyncio.get_running_loop().create_future()

        async def impersonator(reader, writer):
            try:
                received.set_result(await reader.read(64))
            finally:
                writer.close()
                await writer.wait_closed()

        fake = await asyncio.start_server(impersonator, '127.0.0.1', 0, ssl=self.context)
        self.servers.append(fake)
        control, files = listener(), listener()
        client = await start_client(control_listener=control, file_listener=files,
                                    server_host='127.0.0.1', server_port=fake.sockets[0].getsockname()[1],
                                    server_fingerprint='0' * 64, shared_key=b'a' * 32)
        self.tunnels.append(client)
        await self.exchange(control.getsockname()[1], b'command')
        self.assertEqual(await asyncio.wait_for(received, 5), b'')

    async def test_listener_failure_closes_tunnel_and_is_reported(self):
        host = listener()
        tunnel = await start_server(listener=host, ssl_context=self.context,
                                    shared_key=b'a' * 32, control_port=1, file_port=2)
        self.tunnels.append(tunnel)
        loop = asyncio.get_running_loop()
        with patch.object(loop, 'sock_accept', side_effect=OSError('private detail')):
            with self.assertRaisesRegex(RuntimeError, '^tunnel listener stopped$'):
                await asyncio.wait_for(tunnel.wait(), 5)
        self.assertEqual(host.fileno(), -1)

    async def reject_bad_frame(self, frame, *, close_tls):
        received = asyncio.get_running_loop().create_future()

        async def backend(reader, writer):
            try:
                received.set_result(await reader.read())
            finally:
                writer.close()
                await writer.wait_closed()

        service = await asyncio.start_server(backend, '127.0.0.1', 0)
        self.servers.append(service)
        host = listener()
        port = host.getsockname()[1]
        tunnel = await start_server(listener=host, ssl_context=self.context,
                                    shared_key=b'a' * 32,
                                    control_port=service.sockets[0].getsockname()[1],
                                    file_port=service.sockets[0].getsockname()[1])
        self.tunnels.append(tunnel)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        reader, writer = await asyncio.open_connection('127.0.0.1', port, ssl=context, server_hostname='')
        writer.write(b'X4TUN02\0' + b'a' * 32 + b'\x01')
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(1), 5), b'\x01')
        writer.write(frame)
        await writer.drain()
        if close_tls:
            writer.close()
            await writer.wait_closed()
        else:
            self.assertEqual(await asyncio.wait_for(reader.read(), 5), b'')
            writer.close()
            await writer.wait_closed()
        self.assertEqual(await asyncio.wait_for(received, 5), b'')

    async def test_oversize_frame_is_rejected_without_backend_data(self):
        await self.reject_bad_frame(struct.pack('!BI', 1, 65537), close_tls=False)

    async def test_truncated_frame_is_rejected_without_backend_data(self):
        await self.reject_bad_frame(struct.pack('!BI', 1, 4) + b'ab', close_tls=True)


if __name__ == '__main__':
    unittest.main()
