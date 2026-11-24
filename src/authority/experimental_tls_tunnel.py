"""Experimental pinned TLS bridges for the existing loopback TCP services.

The caller owns the process lifetimes, certificate, key distribution and the
local endpoints.  This is a transport experiment, not multiplayer admission.
Each connection authenticates separately and carries exactly one byte stream.
"""

import argparse
import asyncio
import hashlib
import hmac
import ipaddress
import math
from pathlib import Path
import re
import secrets
import socket
import ssl
import struct


_MAGIC = b'X4TUN02\0'
_MAX_FRAME = 64 * 1024
_CONNECT_TIMEOUT = 10.0
_CLOSE_TIMEOUT = 2.0
_HEADER = struct.Struct('!BI')
_DATA, _EOF, _PING, _PONG = 1, 2, 3, 4
_CONTROL, _FILE = 1, 2


def _validate_listener(listener, *, loopback):
    if (type(listener) is not socket.socket or listener.family != socket.AF_INET or
            listener.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM or
            not listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)):
        raise ValueError('an IPv4 listening TCP socket is required')
    if loopback and listener.getsockname()[0] != '127.0.0.1':
        raise ValueError('client tunnel listener must bind to 127.0.0.1')
    listener.setblocking(False)


def _validate_options(shared_key, max_connections, idle_timeout):
    if type(shared_key) is not bytes or len(shared_key) != 32:
        raise ValueError('shared key must contain exactly 32 bytes')
    if type(max_connections) is not int or not 1 <= max_connections <= 64:
        raise ValueError('max_connections must be between 1 and 64')
    if (type(idle_timeout) not in (int, float) or not math.isfinite(idle_timeout)
            or not 1 <= idle_timeout <= 3600):
        raise ValueError('idle timeout must be between 1 and 3600 seconds')


def _port(value):
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError('a valid TCP port is required')
    return value


async def _close_writer(writer):
    if writer is None:
        return
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), _CLOSE_TIMEOUT)
    except (OSError, ssl.SSLError, asyncio.TimeoutError):
        pass


async def _relay(raw_reader, raw_writer, tls_reader, tls_writer, idle_timeout):
    """Relay bounded frames, preserving half-closes and monitoring the peer."""
    loop = asyncio.get_running_loop()
    last_peer_progress = loop.time()
    pending_ping = None
    write_lock = asyncio.Lock()
    pong_queue = asyncio.Queue(maxsize=1)
    local_eof = asyncio.Event()
    remote_eof = asyncio.Event()

    async def send_frame(kind, payload=b''):
        # Data and heartbeat writers share a TLS stream. Hold the lock through
        # drain so backpressure cannot permit overlapping writes.
        async with write_lock:
            tls_writer.write(_HEADER.pack(kind, len(payload)) + payload)
            await tls_writer.drain()

    async def from_raw():
        while True:
            chunk = await raw_reader.read(_MAX_FRAME)
            await send_frame(_DATA if chunk else _EOF, chunk)
            if not chunk:
                local_eof.set()
                return

    async def to_raw():
        nonlocal last_peer_progress, pending_ping
        while True:
            kind, length = _HEADER.unpack(await tls_reader.readexactly(_HEADER.size))
            if kind == _EOF and length == 0 and not remote_eof.is_set():
                raw_writer.write_eof()
                await raw_writer.drain()
                remote_eof.set()
            elif kind == _DATA and 1 <= length <= _MAX_FRAME and not remote_eof.is_set():
                raw_writer.write(await tls_reader.readexactly(length))
                await raw_writer.drain()
            elif kind == _PING and length == 8:
                challenge = await tls_reader.readexactly(length)
                try:
                    pong_queue.put_nowait(challenge)
                except asyncio.QueueFull:
                    raise ValueError('too many pending tunnel heartbeats') from None
            elif kind == _PONG and length == 8 and pending_ping is not None:
                response = await tls_reader.readexactly(length)
                if not hmac.compare_digest(response, pending_ping):
                    raise ValueError('invalid tunnel heartbeat')
                pending_ping = None
            else:
                raise ValueError('invalid tunnel frame')
            last_peer_progress = loop.time()

    async def send_pongs():
        while True:
            challenge = await pong_queue.get()
            await send_frame(_PONG, challenge)

    async def both_directions_finished():
        await local_eof.wait()
        await remote_eof.wait()

    async def watchdog():
        nonlocal pending_ping
        while True:
            await asyncio.sleep(min(1.0, idle_timeout / 3))
            idle = loop.time() - last_peer_progress
            if idle >= idle_timeout:
                raise TimeoutError('tunnel idle timeout')
            if idle >= idle_timeout / 3 and pending_ping is None:
                pending_ping = secrets.token_bytes(8)
                # A blocked drain must not prevent the watchdog from enforcing
                # the deadline, including when another writer holds the lock.
                await asyncio.wait_for(send_frame(_PING, pending_ping), idle_timeout - idle)

    tasks = [asyncio.create_task(from_raw()), asyncio.create_task(to_raw())]
    pong_sender = asyncio.create_task(send_pongs())
    timer = asyncio.create_task(watchdog())
    finished = asyncio.create_task(both_directions_finished())
    try:
        # Continue reading control frames after one direction half-closes.
        pending = set(tasks) | {pong_sender, timer, finished}
        while finished in pending:
            completed, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in completed:
                task.result()
            pending.difference_update(completed)
    finally:
        for task in [*tasks, pong_sender, timer, finished]:
            task.cancel()
        await asyncio.gather(*tasks, pong_sender, timer, finished, return_exceptions=True)


class RunningTunnel:
    """Own listeners and connections until close() has finished."""

    def __init__(self, listeners, limit):
        self._listeners = listeners
        self._slots = asyncio.Semaphore(limit)
        self._accept_tasks = []
        self._connections = set()
        self._closed = False

    async def _accept(self, listener, handler):
        loop = asyncio.get_running_loop()
        while True:
            conn, _ = await loop.sock_accept(listener)
            try:
                await self._slots.acquire()
            except BaseException:
                conn.close()
                raise
            task = asyncio.create_task(self._run_connection(conn, handler))
            self._connections.add(task)
            task.add_done_callback(self._connection_finished)

    def _connection_finished(self, task):
        self._connections.discard(task)
        if not task.cancelled():
            task.exception()  # Do not log network or caller exception contents.

    async def wait(self):
        """Wait for shutdown, or close all resources if a listener fails."""
        await asyncio.wait(self._accept_tasks, return_when=asyncio.FIRST_COMPLETED)
        if self._closed:
            return
        await self.close()
        raise RuntimeError('tunnel listener stopped') from None

    async def _run_connection(self, conn, handler):
        try:
            await handler(conn)
        except (OSError, ssl.SSLError, ValueError, TimeoutError,
                asyncio.IncompleteReadError, asyncio.TimeoutError):
            # Network/authentication errors close this connection only.  Never
            # include supplied bytes or exception text in diagnostic output.
            pass
        finally:
            conn.close()
            self._slots.release()

    async def close(self):
        if self._closed:
            return
        self._closed = True
        for task in self._accept_tasks:
            task.cancel()
        await asyncio.gather(*self._accept_tasks, return_exceptions=True)
        for listener in self._listeners:
            listener.close()
        tasks = tuple(self._connections)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _accepted_stream(conn, *, ssl_context=None):
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=_MAX_FRAME)
    protocol = asyncio.StreamReaderProtocol(reader)
    options = {} if ssl_context is None else {
        'ssl': ssl_context, 'ssl_handshake_timeout': _CONNECT_TIMEOUT}
    transport, _ = await loop.connect_accepted_socket(lambda: protocol, conn, **options)
    return reader, asyncio.StreamWriter(transport, protocol, reader, loop)


def create_server_context(certificate, private_key, *, password=None):
    """Use the tunnel's TLS floor regardless of the platform OpenSSL defaults."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, private_key, password=password)
    return context


def _validate_server_context(context):
    if not isinstance(context, ssl.SSLContext) or context.protocol != ssl.PROTOCOL_TLS_SERVER:
        raise ValueError('server TLS context required')
    if context.minimum_version < ssl.TLSVersion.TLSv1_2:
        raise ValueError('TLS 1.2 or newer required')


def _validate_server_identity(host, fingerprint):
    if (type(host) is not str or not host or len(host) > 253 or
            type(fingerprint) is not str or not re.fullmatch(r'[0-9a-f]{64}', fingerprint)):
        raise ValueError('server host and lowercase SHA-256 fingerprint required')


def _client_context():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # pin verification follows before authentication
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


async def _server_authenticate(reader, shared_key, services):
    auth = await asyncio.wait_for(reader.readexactly(len(_MAGIC) + 33), _CONNECT_TIMEOUT)
    service = auth[-1]
    if (auth[:len(_MAGIC)] != _MAGIC or
            not hmac.compare_digest(auth[len(_MAGIC):-1], shared_key) or
            service not in services):
        raise ValueError('tunnel authentication failed')
    return service


async def _client_authenticate(reader, writer, fingerprint, shared_key, service):
    certificate = writer.get_extra_info('ssl_object').getpeercert(binary_form=True)
    if not certificate or not hmac.compare_digest(hashlib.sha256(certificate).hexdigest(), fingerprint):
        raise ValueError('server fingerprint mismatch')
    writer.write(_MAGIC + shared_key + bytes([service]))
    await asyncio.wait_for(writer.drain(), _CONNECT_TIMEOUT)
    if await asyncio.wait_for(reader.readexactly(1), _CONNECT_TIMEOUT) != b'\x01':
        raise ValueError('tunnel authentication failed')


def _validate_connected_loopback(sock):
    if (type(sock) is not socket.socket or sock.family != socket.AF_INET or
            sock.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM or
            sock.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) or
            sock.getsockname()[0] != '127.0.0.1' or sock.getpeername()[0] != '127.0.0.1'):
        raise ValueError('connected IPv4 loopback TCP socket required')
    sock.setblocking(False)


def _validate_distinct_connections(first, second):
    if first is second or first.fileno() == second.fileno():
        raise ValueError('distinct socket connections required')
    endpoints = (first.getsockname(), first.getpeername())
    other = (second.getsockname(), second.getpeername())
    if endpoints == other or endpoints == other[::-1]:
        raise ValueError('distinct socket connections required')


async def relay_connected_server(*, tls_socket, backend_socket, ssl_context, shared_key, idle_timeout=300):
    """Run one private CONTROL relay; close both sockets after coroutine start.

    The caller owns the sockets until this coroutine first runs and must close
    them if it never starts. Establish and correlate socket pairs before then.
    This does not authenticate other processes on the local machine.
    """
    tls_writer = raw_writer = None
    try:
        _validate_options(shared_key, 1, idle_timeout)
        _validate_server_context(ssl_context)
        _validate_connected_loopback(tls_socket)
        _validate_connected_loopback(backend_socket)
        _validate_distinct_connections(tls_socket, backend_socket)
        tls_reader, tls_writer = await _accepted_stream(tls_socket, ssl_context=ssl_context)
        await _server_authenticate(tls_reader, shared_key, {_CONTROL})
        raw_reader, raw_writer = await asyncio.wait_for(
            asyncio.open_connection(sock=backend_socket), _CONNECT_TIMEOUT)
        tls_writer.write(b'\x01')
        await asyncio.wait_for(tls_writer.drain(), _CONNECT_TIMEOUT)
        await _relay(raw_reader, raw_writer, tls_reader, tls_writer, idle_timeout)
    finally:
        try:
            await _close_writer(raw_writer)
        finally:
            try:
                await _close_writer(tls_writer)
            finally:
                try:
                    if type(tls_socket) is socket.socket:
                        tls_socket.close()
                finally:
                    if type(backend_socket) is socket.socket:
                        backend_socket.close()


async def relay_connected_client(*, raw_socket, tls_socket, server_hostname,
                                 server_fingerprint, shared_key, idle_timeout=300):
    """Run one private CONTROL relay; close both sockets after coroutine start.

    The caller owns the sockets until this coroutine first runs and must close
    them if it never starts. Establish and correlate socket pairs before then.
    """
    raw_writer = tls_writer = None
    try:
        _validate_options(shared_key, 1, idle_timeout)
        _validate_server_identity(server_hostname, server_fingerprint)
        _validate_connected_loopback(raw_socket)
        _validate_connected_loopback(tls_socket)
        _validate_distinct_connections(raw_socket, tls_socket)
        raw_reader, raw_writer = await _accepted_stream(raw_socket)
        tls_reader, tls_writer = await asyncio.wait_for(
            asyncio.open_connection(sock=tls_socket, ssl=_client_context(),
                                    server_hostname=server_hostname,
                                    ssl_handshake_timeout=_CONNECT_TIMEOUT), _CONNECT_TIMEOUT)
        await _client_authenticate(tls_reader, tls_writer, server_fingerprint, shared_key, _CONTROL)
        await _relay(raw_reader, raw_writer, tls_reader, tls_writer, idle_timeout)
    finally:
        try:
            await _close_writer(raw_writer)
        finally:
            try:
                await _close_writer(tls_writer)
            finally:
                try:
                    if type(raw_socket) is socket.socket:
                        raw_socket.close()
                finally:
                    if type(tls_socket) is socket.socket:
                        tls_socket.close()


async def start_server(*, listener, ssl_context, shared_key, control_port, file_port,
                       max_connections=8, idle_timeout=300):
    """Start an authenticated gateway to two fixed server-local TCP ports.

    Takes ownership of the listening socket.  The TLS context must contain the
    operator's certificate and private key; callers distribute its fingerprint.
    """
    _validate_options(shared_key, max_connections, idle_timeout)
    _validate_server_context(ssl_context)
    destinations = {_CONTROL: _port(control_port), _FILE: _port(file_port)}
    _validate_listener(listener, loopback=False)
    tunnel = RunningTunnel([listener], max_connections)

    async def handle(conn):
        tls_writer = raw_writer = None
        try:
            tls_reader, tls_writer = await _accepted_stream(conn, ssl_context=ssl_context)
            service = await _server_authenticate(tls_reader, shared_key, destinations)
            raw_reader, raw_writer = await asyncio.wait_for(
                asyncio.open_connection('127.0.0.1', destinations[service]), _CONNECT_TIMEOUT)
            tls_writer.write(b'\x01')
            await asyncio.wait_for(tls_writer.drain(), _CONNECT_TIMEOUT)
            await _relay(raw_reader, raw_writer, tls_reader, tls_writer, idle_timeout)
        finally:
            await _close_writer(raw_writer)
            await _close_writer(tls_writer)

    tunnel._accept_tasks.append(asyncio.create_task(tunnel._accept(listener, handle)))
    return tunnel


async def start_client(*, control_listener, file_listener, server_host, server_port,
                       server_fingerprint, shared_key, max_connections=8, idle_timeout=300):
    """Start two local endpoints using pinned TLS to one remote gateway."""
    _validate_options(shared_key, max_connections, idle_timeout)
    _port(server_port)
    _validate_server_identity(server_host, server_fingerprint)
    if control_listener is file_listener:
        raise ValueError('separate local listeners required')
    _validate_listener(control_listener, loopback=True)
    _validate_listener(file_listener, loopback=True)
    context = _client_context()
    tunnel = RunningTunnel([control_listener, file_listener], max_connections)

    async def handle(conn, service):
        raw_writer = tls_writer = None
        try:
            raw_reader, raw_writer = await _accepted_stream(conn)
            tls_reader, tls_writer = await asyncio.wait_for(
                asyncio.open_connection(server_host, server_port, ssl=context,
                                        server_hostname='', ssl_handshake_timeout=_CONNECT_TIMEOUT),
                _CONNECT_TIMEOUT)
            await _client_authenticate(tls_reader, tls_writer, server_fingerprint, shared_key, service)
            await _relay(raw_reader, raw_writer, tls_reader, tls_writer, idle_timeout)
        finally:
            await _close_writer(raw_writer)
            await _close_writer(tls_writer)

    for listener, service in ((control_listener, _CONTROL), (file_listener, _FILE)):
        tunnel._accept_tasks.append(asyncio.create_task(
            tunnel._accept(listener, lambda conn, service=service: handle(conn, service))))
    return tunnel

def _bind_listener(host, port):
    ipaddress.IPv4Address(host)
    _port(port)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind((host, port))
        listener.listen(16)
        return listener
    except BaseException:
        listener.close()
        raise


def _read_shared_key(path):
    # Read a bounded amount and never print the contents, even on failure.
    with Path(path).open('rb') as key_file:
        encoded = key_file.read(67)
    if encoded.endswith(b'\r\n'):
        encoded = encoded[:-2]
    elif encoded.endswith(b'\n'):
        encoded = encoded[:-1]
    if not re.fullmatch(rb'[0-9a-f]{64}', encoded):
        raise ValueError('key file must contain 64 lowercase hexadecimal characters')
    return bytes.fromhex(encoded.decode('ascii'))


async def _run_cli(options):
    key = _read_shared_key(options.key_file)
    listeners = []
    tunnel = None
    try:
        if options.mode == 'server':
            context = create_server_context(options.certificate, options.private_key)
            listeners.append(_bind_listener(options.listen_address, options.listen_port))
            tunnel = await start_server(listener=listeners[0], ssl_context=context,
                                        shared_key=key, control_port=options.control_port,
                                        file_port=options.file_port, max_connections=options.max_connections,
                                        idle_timeout=options.idle_timeout)
        else:
            listeners.append(_bind_listener('127.0.0.1', options.control_port))
            listeners.append(_bind_listener('127.0.0.1', options.file_port))
            tunnel = await start_client(control_listener=listeners[0], file_listener=listeners[1],
                                        server_host=options.server_host, server_port=options.server_port,
                                        server_fingerprint=options.fingerprint, shared_key=key,
                                        max_connections=options.max_connections, idle_timeout=options.idle_timeout)
        await tunnel.wait()
    finally:
        if tunnel is not None:
            await tunnel.close()
        for listener in listeners:
            listener.close()


def main():
    parser = argparse.ArgumentParser(description='Experimental X4 pinned TLS TCP tunnel')
    commands = parser.add_subparsers(dest='mode', required=True)
    server = commands.add_parser('server')
    server.add_argument('--listen-address', required=True, help='IPv4 address to expose')
    server.add_argument('--listen-port', type=int, required=True)
    server.add_argument('--certificate', required=True, help='server certificate PEM file')
    server.add_argument('--private-key', required=True, help='server private-key PEM file')
    client = commands.add_parser('client')
    client.add_argument('--server-host', required=True)
    client.add_argument('--server-port', type=int, required=True)
    client.add_argument('--fingerprint', required=True, help='SHA-256 of the DER certificate, lowercase hex')
    for command in (server, client):
        command.add_argument('--key-file', required=True, help='file containing the shared random key')
        command.add_argument('--max-connections', type=int, default=8, help='active connection limit (1-64)')
        command.add_argument('--idle-timeout', type=float, default=300, help='idle timeout in seconds (1-3600)')
        command.add_argument('--control-port', type=int, required=True,
                             help='local control service port (server) or listener port (client)')
        command.add_argument('--file-port', type=int, required=True,
                             help='local file service port (server) or listener port (client)')
    options = parser.parse_args()
    try:
        asyncio.run(_run_cli(options))
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, RuntimeError, ssl.SSLError):
        parser.exit(1, 'Unable to start tunnel; verify the supplied configuration and files.\n')



if __name__ == '__main__':
    main()
