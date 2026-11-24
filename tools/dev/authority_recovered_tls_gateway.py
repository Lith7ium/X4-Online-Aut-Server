"""Own the optional pinned TLS gateway for the recovered Linux server."""
import asyncio
import hashlib
import ipaddress
import json
import os
import re
import socket
import ssl
import threading

from authority.experimental_tls_tunnel import _read_shared_key, create_server_context, start_server


def gateway_settings(args):
    bind = getattr(args, 'tls_bind', None)
    host = getattr(args, 'tls_advertise', None)
    port = getattr(args, 'tls_port', None)
    advertised_port = getattr(args, 'tls_advertise_port', None)
    certificate = getattr(args, 'tls_certificate', None)
    private_key = getattr(args, 'tls_private_key', None)
    shared_key_file = getattr(args, 'tls_shared_key_file', None)
    required = (bind, host, port, certificate, private_key, shared_key_file)
    if advertised_port is None and all(value is None for value in required):
        return None
    if any(value is None for value in required):
        raise ValueError('TLS requires bind, advertised host, port, certificate, private key and shared key file')
    if (getattr(args, 'registry_witness', None) is not None or
            getattr(args, 'registry_witness_join', False) or
            getattr(args, 'bootstrap_registry_snapshot', False)):
        raise ValueError('TLS requires a mode that serves client connections')
    try:
        bind = str(ipaddress.IPv4Address(bind))
    except ipaddress.AddressValueError:
        raise ValueError('TLS bind address must be IPv4') from None
    if (type(host) is not str or not 1 <= len(host) <= 253 or host == '0.0.0.0' or
            any(not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', label)
                for label in host.removesuffix('.').split('.'))):
        raise ValueError('TLS advertised address must be an IPv4 address or DNS hostname')
    if (type(port) is not int or not 0 <= port <= 65535 or
            (advertised_port is not None and (type(advertised_port) is not int or not 1 <= advertised_port <= 65535))):
        raise ValueError('invalid TLS port')
    try:
        if not all(path.is_file() for path in (certificate, private_key, shared_key_file)):
            raise ValueError('ordinary credential files required')
        cert_limit = 1 << 20
        with certificate.open('rb') as stream:
            pem = stream.read(cert_limit + 1)
        if len(pem) > cert_limit:
            raise ValueError('certificate file too large')
        leaf = re.search(rb'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', pem, re.DOTALL)
        if leaf is None:
            raise ValueError('certificate missing')
        fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(leaf[0].decode('ascii'))).hexdigest()
        context = create_server_context(certificate, private_key, password='')
        with certificate.open('rb') as stream:
            if stream.read(cert_limit + 1) != pem:
                raise ValueError('certificate changed while loading')
        shared_key = _read_shared_key(shared_key_file)
    except (OSError, ValueError):
        raise ValueError('TLS credentials are missing, invalid or changed during loading') from None
    return bind, port, host, advertised_port, fingerprint, context, shared_key


class RecoveredTLSGateway:
    """Run the async gateway alongside the main thread's synchronous coordinator."""

    def __init__(self, settings, *, out, control_port, file_port, file_token):
        self.connection = out / 'connection.json'
        self._settings = settings
        self._control_port = control_port
        self._file_port = file_port
        self._file_token = file_token
        self._ready = threading.Event()
        self._failed = False
        self._published = False
        self._listener = None
        self._stop = None
        self._thread = None

    async def _run(self, wakeup):
        _, _, _, _, _, context, key = self._settings
        tunnel = await start_server(listener=self._listener, ssl_context=context, shared_key=key,
                                    control_port=self._control_port, file_port=self._file_port)
        waiter = asyncio.create_task(tunnel.wait())
        stop = asyncio.create_task(asyncio.get_running_loop().sock_recv(wakeup, 1))
        try:
            await asyncio.sleep(0)
            self._ready.set()
            finished, _ = await asyncio.wait((waiter, stop), return_when=asyncio.FIRST_COMPLETED)
            if waiter in finished:
                waiter.result()
                raise RuntimeError('TLS listener stopped')
            stop.result()
        finally:
            stop.cancel()
            await asyncio.gather(stop, return_exceptions=True)
            try:
                await tunnel.close()
            finally:
                # The monitor may own cleanup after an accept failure.
                await asyncio.gather(waiter, return_exceptions=True)

    def _worker(self, wakeup):
        with wakeup:
            try:
                asyncio.run(self._run(wakeup))
            except Exception:
                self._failed = True
            finally:
                self._ready.set()

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive() and not self._failed

    def start(self, timeout):
        bind, requested_port, host, port, fingerprint, _, key = self._settings
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self._listener.bind((bind, requested_port))
            self._listener.listen(16)
            wakeup, self._stop = socket.socketpair()
            wakeup.setblocking(False)
            thread = threading.Thread(target=self._worker, args=(wakeup,), daemon=True)
            try:
                thread.start()
            except BaseException:
                wakeup.close()
                raise
            self._thread = thread
        except OSError:
            raise RuntimeError('cannot start TLS gateway listener') from None
        if not self._ready.wait(timeout) or not self.running:
            raise RuntimeError('TLS gateway did not become ready')
        port = port if port is not None else self._listener.getsockname()[1]
        payload = {'server_host': host, 'server_port': port, 'server_fingerprint': fingerprint,
                   'shared_key': key.hex(), 'file_token': self._file_token, 'file_slot': 1}
        temporary = self.connection.with_suffix('.json.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'w', encoding='ascii') as stream:
                json.dump(payload, stream)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            if not self.running:
                raise RuntimeError('TLS gateway stopped before publishing the connection file')
            os.link(temporary, self.connection, follow_symlinks=False)
            self._published = True
        finally:
            temporary.unlink()

    def close(self):
        try:
            if self._published:
                self.connection.unlink(missing_ok=True)
                self._published = False
        finally:
            if self._stop is not None:
                self._stop.close()
                self._stop = None
            try:
                if self._thread is not None:
                    self._thread.join(6)
                    if self._thread.is_alive():
                        raise RuntimeError('TLS gateway cleanup timed out')
                    self._thread = None
            finally:
                if self._listener is not None:
                    self._listener.close()
                    self._listener = None
