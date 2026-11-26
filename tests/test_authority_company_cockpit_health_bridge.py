"""Mailbox freshness and the real process-owned Company motion UDP route."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import socket
import unittest
from unittest.mock import patch

from authority.company_cockpit_health import CompanyCockpitHealthBridge
from authority.company_motion_source import CompanyMotionSource

EPOCH, GENERATION = 'b' * 32, 'c' * 32
INDEX = 1074
STATE = dict(shield=0, maxshield=2366, hull=3578, maxhull=3600, revision=0)
ROOT = Path(__file__).resolve().parents[1]


class CompanyCockpitHealthBridgeTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.binding = GENERATION, EPOCH, INDEX

    def bridge(self, binding):
        return CompanyCockpitHealthBridge(binding, clock=lambda: self.now)

    def test_bound_requests_reject_old_replies_and_use_original_expiration(self):
        bridge = self.bridge(self.binding)
        bridge.on_sample(dict(sequence=1, capture_time=100))
        self.assertIsNone(bridge.take_request())
        bridge.on_clock(3, True)
        bridge.on_sample(dict(sequence=1, capture_time=99.9))
        self.assertIsNone(bridge.take_request())
        bridge.on_sample(dict(sequence=2, capture_time=100))
        with ThreadPoolExecutor(max_workers=1) as worker:
            self.assertEqual(worker.submit(bridge.take_request).result(1), (3, 1, None))
            self.assertIsNone(worker.submit(bridge.take_request).result(1))
            self.assertFalse(worker.submit(bridge.accept_reply, 2, 1, STATE).result(1))
            self.now = 100.9
            self.assertTrue(worker.submit(bridge.accept_reply, 3, 1, STATE).result(1))

        messages = []
        bridge.deliver(lambda message: not messages.append(message))
        fields = messages[1].split('|')
        self.assertEqual(fields[:8], ['HEALTH', '1', GENERATION, EPOCH, str(INDEX), '3', '1', '2'])
        self.assertEqual(list(map(float, fields[8:])), [102, 0, 2366, 3578, 3600])
        self.assertIsNone(bridge.take_request())
        self.now = 102
        bridge.on_sample(dict(sequence=3, capture_time=102))
        self.assertFalse(bridge.accept_reply(3, 1, STATE))
        self.assertEqual(bridge.take_request(), (3, 2, None))
        bridge.on_clock(4, False)
        self.assertFalse(bridge.accept_reply(3, 2, STATE))
        self.assertIsNone(bridge.take_request())
        bridge.on_clock(5, True)
        bridge.on_sample(dict(sequence=4, capture_time=102))
        self.assertEqual(bridge.take_request(), (5, 3, None))

    def test_exact_socket_and_native_receipt_gate_the_cockpit_bridge(self):
        source = CompanyMotionSource()
        self.addCleanup(source.close)
        source.enable_cockpit_health()
        config = source.configure(EPOCH, INDEX)
        self.assertTrue(config['cockpit_health'])
        cue = source.cockpit_health_md((ROOT / 'src/authority/md/company_cockpit_health.xml').read_bytes())
        self.assertIn(config['lease'].encode(), cue)
        self.assertNotIn(b'__COCKPIT_HEALTH_LEASE__', cue)
        with patch('authority.company_motion_source.CompanyCockpitHealthBridge', side_effect=self.bridge):
            bridge = source.bind_cockpit_health(dict(epoch=EPOCH, generation=GENERATION, index=INDEX))
        bridge.on_clock(3, True)
        overlay = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        foreign = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for peer in (overlay, foreign):
            self.addCleanup(peer.close)
            peer.bind(('127.0.0.1', 0))
            peer.settimeout(.1)
        prefix = f'C|{config["lease"]}|{config["generation"]}|'.encode()
        destination = source.socket.getsockname()
        allowed = overlay.getsockname()

        def send(message, peer=overlay):
            peer.sendto(prefix + message.encode(), destination)

        def receive():
            row, address = overlay.recvfrom(576)
            self.assertEqual(address, destination)
            self.assertTrue(row.startswith(prefix))
            return row[len(prefix):].decode()

        def poll():
            return source.poll(lambda address: address == allowed)

        sample = f'SAMPLE3|{EPOCH}|{INDEX}|1|sector_a|1|100|0|0|0|-|-|-|'
        send(sample, foreign)
        self.assertIsNone(poll())
        self.assertIsNone(bridge.take_request())
        send(sample)
        self.assertEqual(poll()['sequence'], 1)
        self.assertEqual(receive(), 'HEALTH_CLEAR|1')
        self.assertEqual(bridge.take_request(), (3, 1, None))
        self.assertTrue(bridge.accept_reply(3, 1, STATE))
        poll()
        self.assertRegex(receive(), re.escape(f'HEALTH|1|{GENERATION}|{EPOCH}|{INDEX}|3|1|1|102|') + r'0\|2366\|3578\|3600$')
        result = f'HEALTH_RESULT|{GENERATION}|{EPOCH}|{INDEX}|3|1|confirmed'
        send(result, foreign)
        poll()
        self.assertEqual(bridge.stats()['confirmed'], 0)
        send(result)
        poll()
        self.assertEqual(bridge.stats()['confirmed'], 1)
        self.now = 101
        self.assertEqual(bridge.take_request(), (3, 2, 0))
        self.assertTrue(bridge.accept_reply(3, 2, STATE))  # A new TLS stream can serve a fresh equal-revision read.
        send('GAP')
        poll()
        self.assertEqual(receive(), 'HEALTH_CLEAR|2')
        self.assertIsNone(bridge.take_request())
        self.assertFalse(bridge.accept_reply(3, 1, STATE))

    def test_unknown_local_write_fences_any_later_requests(self):
        bridge = self.bridge(self.binding)
        bridge.on_clock(3, True)
        bridge.on_sample(dict(sequence=1, capture_time=100))
        self.assertEqual(bridge.take_request(), (3, 1, None))
        self.assertTrue(bridge.accept_reply(3, 1, STATE))
        bridge.deliver(lambda message: True)
        self.now = 102
        bridge.native_result(['HEALTH_RESULT', GENERATION, EPOCH, str(INDEX), '3', '1', 'unknown'])
        bridge.on_clock(5, True)
        bridge.on_sample(dict(sequence=2, capture_time=100))
        self.assertIsNone(bridge.take_request())
        self.assertEqual(bridge.stats()['unknown'], 1)
