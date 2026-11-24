"""TLS relay composition with real load and participant code, simulated X4.

The embedded certificate and key are public test fixtures, never production secrets.
This test uses local sockets; it does not test Windows, the Internet, or X4.
"""
import asyncio
import hashlib
from pathlib import Path
import socket
import ssl
import sys
import threading
import time
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests'), str(ROOT / 'tools/dev')]

import test_authority_experimental_local_host as local_host_fixture
from authority_recovered_tls_gateway import RecoveredTLSGateway, gateway_settings
from authority_windows_fixture_client import read_connection_settings
from authority.experimental_remote_load_client import ExperimentalRemoteLoadClient
from authority.experimental_tcp_participant_client import ExperimentalTCPParticipantClient
from authority.experimental_tcp_participants import ExperimentalTCPParticipants
from authority.experimental_tls_tunnel import create_server_context, start_client, start_server
from lockstep.launcher.joinfiles import FetchError, request_line

TEST_CERTIFICATE = """-----BEGIN CERTIFICATE-----
MIIC6DCCAdCgAwIBAgIUHXfTg6Pr83v8s6ObQIDyk8jTfgIwDQYJKoZIhvcNAQEL
BQAwFDESMBAGA1UEAwwJbG9jYWxob3N0MB4XDTI2MTExNzA3NDkzOFoXDTM2MTEx
NDA3NDkzOFowFDESMBAGA1UEAwwJbG9jYWxob3N0MIIBIjANBgkqhkiG9w0BAQEF
AAOCAQ8AMIIBCgKCAQEAnzyFKxczQKRa1lyDzZdBTp5jf/xf10SkTmT2oeufBjGr
L3RBaXTUOBJlZUqqSKfLSVdUpVYUSO4gXMjhdhofghMjAPOs/xx8+wZoKQs41+1V
z+YDZI5gFJdqY4Ll3znGXnFBx7AaUu/4BxTwRRceTMNRpfSiMwhbVpk7mA4J+/0c
Zl/GGHCOR/5ucwi5O9giyFBRV6gfYdprVZAQZt7gMDYypUtEBE2ENp0CnHXqK9XB
mLxDSHeCm4z9elYm06sVHx1SnZhqdl4eMoPzxh4zToebo1f/BKX2jwXELAN3BObZ
nexBDa1HX2U7qB6QqDQBytj6oMEzkhw45GrDAbdviQIDAQABozIwMDAdBgNVHQ4E
FgQUyUIxqDkri5sNk1FF2vd6bCpyCBEwDwYDVR0TAQH/BAUwAwEB/zANBgkqhkiG
9w0BAQsFAAOCAQEAl1mErG/J6eznGQy2c7jpcbgYDNM1ySjTy99NzfbYqMmKvlWT
LG4Me93y47tUwnLaSdMwLQAILEAWQOkEhK8TuCqk8Y4hydcpe7PbnBRhfwF/guI6
8fYYLpzb/SK4LTKxbyWYO1Anr371erB+4xHcdwWC8pbOB3II47xgIDw7XYB9wwsP
A+JKELSDLcI9LMgi3hthnPHsyIDzv2fyErln5g5wD7JzenoHZonKmWEJns10iIVU
VgplCg4ufVi10BI82zl0zKLpMoY93OWIguIi6qwOBx5cOkMLIlxacRgw84vnmaz1
srya+ooBfPUyOGOWj5pHikrJHjUO4PfAAuLoQg==
-----END CERTIFICATE-----
"""
TEST_PRIVATE_KEY = """-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQCfPIUrFzNApFrW
XIPNl0FOnmN//F/XRKROZPah658GMasvdEFpdNQ4EmVlSqpIp8tJV1SlVhRI7iBc
yOF2Gh+CEyMA86z/HHz7BmgpCzjX7VXP5gNkjmAUl2pjguXfOcZecUHHsBpS7/gH
FPBFFx5Mw1Gl9KIzCFtWmTuYDgn7/RxmX8YYcI5H/m5zCLk72CLIUFFXqB9h2mtV
kBBm3uAwNjKlS0QETYQ2nQKcdeor1cGYvENId4KbjP16VibTqxUfHVKdmGp2Xh4y
g/PGHjNOh5ujV/8EpfaPBcQsA3cE5tmd7EENrUdfZTuoHpCoNAHK2PqgwTOSHDjk
asMBt2+JAgMBAAECggEAGlx5CVCubfGnjhxDv6neKGdFPp9nuGoHL2k8gULSEULw
vYrn907dgSjee13Yge2xVENuYm61LfuqBOJGnPgnJjAH1/T/Oz6anC4L2VufvfNv
BNtlzWRamJ4WgUHez00vv1vNkYLgYMGUVIvNWQrCO+AL6MbugTdNc7ErcflFAt+5
xp09sXXf9M0XTwgryIFiGpXrvna9xXMJYzqCHCz5qO0tH/2x/pSualWjyQTrcBmT
S4wh/UoH7pgqIygLriu6nby6KCe5NSh/LpXv8FvXKRE2pHFvZJ4cI5Hr+P4PaMSq
sUE0vcshfERcYt0euzeQy/wZIvnhTxxqonAhXjHX3wKBgQDSlxSgPwkH5fcGZ4Gx
aCz74/e7i5PaBg56CGXe2EHNc1vOHnTyGnle97RIIn8YIH0G9hsu+QwkctolBnm1
z1ijVEQXTPW6uQgmH3MzCIyfIgYekG/lqcIn6wHcuok3qjbYOMbB66StAboy3bsi
qCC1jtl+fapok5o1PBBq4TtQRwKBgQDBkqEEQZbG6aGo6ARBrdE+jx5I1854P+xL
wrJeCsuDXJNGQu70Potddjc9ZILNWS0deWLJRDMBLX9/AYxQl2s8lc83x0bbFmFw
tG7By5n3cUozAPLWwV2E0VdEI/Qb6vLvZ8SZ/xp4VOrzen5ip5jKEXs0keq+v4+3
rvENu/Z5rwKBgQCKcsUerRJ03N1QmpALYF/iondmF1d2yPCPjiHbJfXs6mB63E8q
M/mDU/p0SimgZQuNnhM1CIrRE6kTGnS98Llnd6jkYLZUsf0HgzmrXX2FyUqYoYDP
TpHUyI4K3aAEtqUj9f/F0fQLwcz2UAwNttb6RhX7MQbthcymHvz0J2tlVQKBgA/G
gGLVaZFKZwdZ6UblAIGFrmUnWUdJhltiQaESZiWNfN8kA09abYcvZC8XquVLWDbn
tppKeZGmAJb8LFBBFtc4KkwUbHzcZRKHUeNvUVvuGqrvb9r6pUF2whLmjzVbP3Mt
VMNsG98kpIU1PWmS2QvWwuC055mmCPNiSXjiEaM3AoGAYiK782+KXkJ2+7u5AzC4
fz7KxVCwHkEbG6E4ykLOLvAZe/6bIf7eBCpNnaZQ9YXCi2f7lmQQsDi8lK3vfoy0
bWd1Ivg334Xy2ZEXHzsmj3yVt7r7mML6ld3F9gfuiHcxqkZHy4UdQjiHp7Tv51A1
AqmGFB1Cc1Bvyi095ZJVjX4=
-----END PRIVATE KEY-----
"""


def listening_socket():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    listener.listen(8)
    return listener


class Controller:
    """Receipts intentionally stand in for actual X4 process observations."""
    def __init__(self):
        self.operations = []

    def pause(self, handles):
        self.operations.append(('pause', handles))
        return True

    def resume(self, handles):
        self.operations.append(('resume', handles))
        return True


class RelayIntegrationTests(unittest.TestCase):
    def test_real_load_and_participant_handoff_through_tls(self):
        self._run_load_case(invalid_file_token=False)

    def test_file_service_refusal_reaches_real_load_client(self):
        self._run_load_case(invalid_file_token=True)

    def test_owned_gateway_publishes_windows_settings_and_releases_listener(self):
        self._run_load_case(invalid_file_token=False, owned_gateway=True)

    def _run_load_case(self, *, invalid_file_token, owned_gateway=False):
        fixture = local_host_fixture.HostTest(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.world._participants = ExperimentalTCPParticipants(fixture.host, enabled=True, timeout=5)
        fixture.listener.settimeout(5)

        root = Path(fixture.temp.name)
        certificate = root / 'test-cert.pem'
        private_key = root / 'test-key.pem'
        certificate.write_text(TEST_CERTIFICATE, encoding='ascii')
        private_key.write_text(TEST_PRIVATE_KEY, encoding='ascii')
        settings = {'server_host': '127.0.0.1',
                    'server_fingerprint': hashlib.sha256(ssl.PEM_cert_to_DER_cert(TEST_CERTIFICATE)).hexdigest(),
                    'shared_key': b'k' * 32, 'file_token': fixture.service.token, 'file_slot': 1}
        control_port = fixture.listener.getsockname()[1]
        if owned_gateway:
            key_file = root / 'test-shared-key'
            key_file.write_text(settings['shared_key'].hex(), encoding='ascii')
            args = SimpleNamespace(tls_bind='127.0.0.1', tls_port=0, tls_advertise='127.0.0.1',
                                   tls_certificate=certificate, tls_private_key=private_key)
            with self.assertRaisesRegex(ValueError, 'requires bind'):
                gateway_settings(args)
            args.tls_shared_key_file = key_file
            options = gateway_settings(args)
            with listening_socket() as occupied:
                args.tls_port = occupied.getsockname()[1]
                unavailable = RecoveredTLSGateway(gateway_settings(args), out=root, control_port=control_port,
                    file_port=fixture.service.port, file_token=fixture.service.token)
                try:
                    with self.assertRaisesRegex(RuntimeError, 'cannot start'):
                        unavailable.start(5)
                    self.assertFalse(unavailable.connection.exists())
                finally:
                    unavailable.close()
            gateway = RecoveredTLSGateway(options, out=root, control_port=control_port,
                                          file_port=fixture.service.port, file_token=fixture.service.token)
            self.addCleanup(gateway.close)
            self.assertFalse(gateway.connection.exists())
            gateway.start(5)
            settings = read_connection_settings(gateway.connection)
            self.assertEqual(gateway.connection.stat().st_mode & 0o777, 0o600)
        else:
            server_context = create_server_context(certificate, private_key)
            server_listener = listening_socket()
            self.addCleanup(server_listener.close)
            settings['server_port'] = server_listener.getsockname()[1]
        control_listener = listening_socket()
        file_listener = listening_socket()
        self.addCleanup(control_listener.close)
        self.addCleanup(file_listener.close)
        local_control_port = control_listener.getsockname()[1]
        local_file_port = file_listener.getsockname()[1]

        loop = asyncio.new_event_loop()
        loop_started = threading.Event()
        def run_loop():
            asyncio.set_event_loop(loop)
            loop_started.set()
            loop.run_forever()
            loop.close()
        loop_thread = threading.Thread(target=run_loop, daemon=True)
        loop_thread.start()
        self.assertTrue(loop_started.wait(3))
        async def make_tunnels():
            server = None
            if not owned_gateway:
                server = await start_server(listener=server_listener, ssl_context=server_context,
                    shared_key=settings['shared_key'], control_port=control_port,
                    file_port=fixture.service.port, idle_timeout=30)
            try:
                client = await start_client(control_listener=control_listener, file_listener=file_listener,
                    server_host=settings['server_host'], server_port=settings['server_port'],
                    server_fingerprint=settings['server_fingerprint'], shared_key=settings['shared_key'], idle_timeout=30)
            except BaseException:
                if server is not None:
                    await server.close()
                raise
            return server, client
        tunnels = None
        def close_tunnels():
            async def close():
                if tunnels is not None:
                    await tunnels[1].close()
                    if tunnels[0] is not None:
                        await tunnels[0].close()
            try:
                asyncio.run_coroutine_threadsafe(close(), loop).result(timeout=8)
            finally:
                loop.call_soon_threadsafe(loop.stop)
                loop_thread.join(8)
        self.addCleanup(close_tunnels)
        tunnels = asyncio.run_coroutine_threadsafe(make_tunnels(), loop).result(timeout=5)

        completed = threading.Event()
        release = threading.Event()
        results, errors = [], []
        def run_client():
            load = participant = None
            try:
                connection = socket.create_connection(('127.0.0.1', local_control_port), timeout=5)
                load = ExperimentalRemoteLoadClient(connection, enabled=True,
                    file_host='127.0.0.1', file_port=local_file_port,
                    file_token='b' * 32 if invalid_file_token else settings['file_token'],
                    file_slot=settings['file_slot'], deadline=time.monotonic()+15, io_timeout=5)
                bound = load.request_offer()
                save = load.download(root / 'tls-download')
                downloaded = save.read_bytes()
                load.verify_staged(save)
                attempt = bound.offer.attempt_id
                load.load_requested(attempt)
                load.loaded_marker(attempt, bound.marker)
                load.paused(attempt, True)
                load.claim(attempt)
                controller = Controller()
                participant = ExperimentalTCPParticipantClient(load.take_connection(), controller,
                    enabled=True, epoch=bound.offer.world_epoch,
                    handle=bound.offer.connection_handle, timeout=5)
                deadline = time.monotonic() + 5
                receipt_sent = False
                while not receipt_sent and time.monotonic() < deadline:
                    receipt_sent = participant.poll(.1)
                if not receipt_sent:
                    raise TimeoutError('participant resume receipt was not sent')
                results.append((downloaded, bound.offer.size, controller.operations))
            except BaseException as error:
                errors.append(error)
            finally:
                completed.set()
                release.wait(5)
                if participant is not None:
                    participant.close()
                if load is not None:
                    load.close()
        worker = threading.Thread(target=run_client, daemon=True)
        fixture.host.tick()
        worker.start()
        try:
            server_connection, _ = fixture.listener.accept()
            fixture.host.adopt(server_connection, defer_attach=owned_gateway)
            deadline = time.monotonic() + 12
            while not completed.is_set() and time.monotonic() < deadline:
                fixture.host.tick(.05)
            self.assertTrue(completed.is_set(), 'client did not complete')
            if invalid_file_token:
                self.assertEqual(len(errors), 1, repr(errors))
                self.assertIsInstance(errors[0], FetchError)
                self.assertIn('refused by the host (mac)', str(errors[0]))
                self.assertEqual(results, [])
                with fixture.service.lock:
                    self.assertTrue(any(reason == 'mac' for _, reason in fixture.service.refused))
                # The load client closes immediately after reading the refusal,
                # so check the FileService write-half closure on another tunneled
                # connection while keeping the request direction open.
                with socket.create_connection(('127.0.0.1', local_file_port), timeout=3) as probe:
                    probe.settimeout(3)
                    probe.sendall(request_line('b' * 32, 1, 'j1.xml.gz'))
                    response = bytearray()
                    while True:
                        chunk = probe.recv(128)
                        if not chunk:
                            break
                        response.extend(chunk)
                        if len(response) > 128:
                            self.fail('unexpectedly long file refusal')
                    self.assertEqual(bytes(response), b'NO|mac\n')
                    probe.sendall(b'drain-probe')
                return
            self.assertFalse(errors, repr(errors))
            self.assertEqual(len(results), 1)
            data, offered_size, operations = results[0]
            self.assertEqual(len(data), offered_size)
            handles = tuple(player[0] for player in fixture.world.session.ready_players)
            self.assertEqual(len(handles), 1)
            self.assertEqual(operations, [('resume', handles)])
            # The handler records a completed transfer after its last send;
            # the client can finish consuming those bytes before that record.
            report_deadline = time.monotonic() + 2
            while True:
                with fixture.service.lock:
                    served = bool(fixture.service.served)
                if served or time.monotonic() >= report_deadline:
                    break
                time.sleep(.01)
            self.assertTrue(served, 'actual FileService did not serve a file')
        finally:
            release.set()
            worker.join(8)
            self.assertFalse(worker.is_alive())
        if owned_gateway:
            deadline = time.monotonic() + 3
            while fixture.world.session.state != 'paused' and time.monotonic() < deadline:
                fixture.host.tick(.05)
            self.assertEqual(fixture.world.session.players, ())
            self.assertEqual(fixture.engine.calls, ['pause', 'save', 'resume', 'pause', 'save'])
            self.assertIsNotNone(fixture.store.latest_marked_confirmed())
            gateway.close()
            self.assertFalse(gateway.connection.exists())
            with self.assertRaises(ConnectionRefusedError):
                socket.create_connection(('127.0.0.1', settings['server_port']), timeout=1)


if __name__ == '__main__':
    unittest.main()
