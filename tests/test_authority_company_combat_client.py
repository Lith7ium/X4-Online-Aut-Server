"""Exercise expired health exchanges on real loopback TCP streams."""
from concurrent.futures import ThreadPoolExecutor
import json
import socket
from threading import Event
import unittest
from unittest.mock import patch

from authority.company_combat_client import CompanyCombatClient, CombatExchangeCancelled
from authority.company_combat_frames import BINDING_FIELDS, encode_frame


class LateHealthTransportTest(unittest.TestCase):
    def setUp(self):
        self.listener = socket.socket()
        self.addCleanup(self.listener.close)
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen()
        self.listener.settimeout(2)
        self.client = CompanyCombatClient(self.listener.getsockname()[1], dict(
            ticket='f' * 64, generation='a' * 32, epoch='b' * 32, index=1073, session_epoch='c' * 32))
        self.addCleanup(self.client.close)
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(self.worker.shutdown)
        limit = patch('authority.company_combat_client._HEALTH_TIMEOUT', .15)
        limit.start()
        self.addCleanup(limit.stop)

    @staticmethod
    def read(conn):
        received = bytearray()
        while not received.endswith(b'\n'):
            part = conn.recv(4096)
            if not part:
                raise AssertionError('connection closed during request')
            received.extend(part)
        return json.loads(received)

    @staticmethod
    def health(request, *, shield=10, **changes):
        result = {key: request[key] for key in (*BINDING_FIELDS, 'sequence', 'window')}
        result.update(kind='combat_health', revision=0,
                      health=dict(shield=shield, maxshield=100, hull=1000, maxhull=1000))
        result.update(changes)
        return encode_frame(result)

    def accept(self):
        peer, _ = self.listener.accept()
        peer.settimeout(2)
        return peer

    def test_partial_late_reply_is_discarded_and_next_read_reuses_one_connection(self):
        timeout = Event()

        def serve():
            with self.accept() as peer:
                request = self.read(peer)
                late = self.health(request)
                peer.sendall(late[:24])
                if not timeout.wait(2):
                    raise AssertionError('client did not time out')
                peer.sendall(late[24:])
                next_request = self.read(peer)
                peer.sendall(self.health(next_request, shield=82))
                return request['sequence'], next_request['sequence']

        server = self.worker.submit(serve)
        try:
            with self.assertRaises(TimeoutError):
                self.client.read_health(3, 1)
            stream = self.client._connection
            self.assertIsNotNone(stream)
            self.assertTrue(self.client._incoming)
        finally:
            timeout.set()
        self.assertEqual(self.client.read_health(4, 2)['shield'], 82)
        self.assertIs(self.client._connection, stream)
        self.assertEqual(server.result(timeout=2), (1, 2))

    def test_impact_reconnects_without_waiting_for_a_stale_reply(self):
        def serve():
            with self.accept() as old:
                self.assertEqual(self.read(old)['kind'], 'combat_health_read')
                # Never send a health reply. The impact must use another socket.
                with self.accept() as fresh:
                    request = self.read(fresh)
                    self.assertEqual(request['kind'], 'combat_impacts')
                    receipt = dict(request['impacts'][0], status='confirmed', shield=74, hull=1000, order=1)
                    fresh.sendall(encode_frame(dict({key: request[key] for key in BINDING_FIELDS},
                                                   kind='combat_receipts', receipts=[receipt])))
                self.assertEqual(old.recv(1), b'')

        server = self.worker.submit(serve)
        with self.assertRaises(TimeoutError):
            self.client.read_health(3, 1)
        impacts = [dict(id=1, target_index=1074, amount=26)]
        self.assertEqual(self.client.exchange(impacts, timeout=1)[0]['status'], 'confirmed')
        server.result(timeout=2)

    def test_invalid_late_reply_closes_before_sending_next_read(self):
        expired = Event()

        def serve():
            with self.accept() as peer:
                request = self.read(peer)
                if not expired.wait(2):
                    raise AssertionError('client did not time out')
                peer.sendall(self.health(request, window=9))
                self.assertEqual(peer.recv(1), b'')

        server = self.worker.submit(serve)
        try:
            with self.assertRaises(TimeoutError):
                self.client.read_health(3, 1)
        finally:
            expired.set()
        with self.assertRaisesRegex(ValueError, 'does not match'):
            self.client.read_health(3, 2)
        self.assertIsNone(self.client._connection)
        server.result(timeout=2)

    def test_cancelled_late_drain_closes_without_sending_again(self):
        def serve():
            with self.accept() as peer:
                self.read(peer)
                self.assertEqual(peer.recv(1), b'')

        server = self.worker.submit(serve)
        with self.assertRaises(TimeoutError):
            self.client.read_health(3, 1)
        with self.assertRaises(CombatExchangeCancelled):
            self.client.read_health(3, 2, cancelled=lambda: True)
        self.assertIsNone(self.client._connection)
        server.result(timeout=2)

    def test_timeout_while_draining_closes_without_sending_a_new_request(self):
        def serve():
            with self.accept() as peer:
                self.assertEqual(self.read(peer)['sequence'], 1)
                self.assertEqual(peer.recv(1), b'')

        server = self.worker.submit(serve)
        with self.assertRaises(TimeoutError):
            self.client.read_health(3, 1)
        with self.assertRaises(TimeoutError):
            self.client.read_health(3, 2)
        self.assertIsNone(self.client._connection)
        server.result(timeout=2)

    def test_partial_request_write_timeout_closes_and_is_never_parked(self):
        original_connect = self.client._connect

        class PartialSocket:
            def __init__(self, stream):
                self.stream = stream
                self.sent_prefix = False

            def __getattr__(self, name):
                return getattr(self.stream, name)

            def send(self, data):
                if self.sent_prefix:
                    raise BlockingIOError
                self.sent_prefix = True
                return self.stream.send(data[:7])

        def partial_connect(*args):
            original_connect(*args)
            self.client._connection = PartialSocket(self.client._connection)

        def serve():
            with self.accept() as peer:
                parts = []
                while part := peer.recv(1024):
                    parts.append(part)
                return b''.join(parts)

        server = self.worker.submit(serve)
        with patch.object(self.client, '_connect', side_effect=partial_connect):
            with self.assertRaises(TimeoutError):
                self.client.read_health(3, 1)
        self.assertIsNone(self.client._connection)
        self.assertEqual(server.result(timeout=2), b'{"gener')


if __name__ == '__main__':
    unittest.main()
