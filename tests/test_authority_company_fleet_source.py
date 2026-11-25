"""Exercise the fleet order adapter with synthetic stock Lua and owned loopback UDP."""

from pathlib import Path
import shutil
import socket
import subprocess
import unittest

from authority.company_fleet_source import CompanyFleetSource


ROOT = Path(__file__).resolve().parents[1]


class CompanyFleetSourceTests(unittest.TestCase):
    def setUp(self):
        self.source = CompanyFleetSource()
        self.addCleanup(self.source.close)
        self.config = self.source.configure('0' * 32)
        self.sender = self.local_socket()
        self.validator = self.sender.getsockname()
        self.source.bind_validator(lambda peer: peer == self.validator)
        self.lease = self.config['lease']

    def local_socket(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(sock.close)
        sock.bind(('127.0.0.1', 0))
        sock.settimeout(0.5)
        return sock

    def packet(self, count=1, x='-1200.25', *, index='2', lease=None, mode=None):
        order_id = f'{self.lease[:24]}{count:08x}'
        order = f'F|{lease or self.lease}|ORDER|{order_id}|{index}|WRN-302|cluster_01_sector001_macro|{x}|44.5|1e-05'
        return (order if mode is None else f'{order}|{mode}').encode()

    def send(self, packet, sock=None):
        (sock or self.sender).sendto(packet, self.source.socket.getsockname())

    def receive(self):
        return self.sender.recvfrom(512)[0].decode().split('|')[2:]

    def test_retries_are_byte_idempotent_while_pending_and_after_receipt(self):
        self.send(self.packet())
        self.send(self.packet())
        request = self.source.exchange(None)
        self.assertEqual(request['ship_index'], 2)
        self.assertEqual(request['x'], -1200.25)
        self.assertEqual(request['z'], 0.00001)
        self.send(self.packet(x='0'))
        self.send(self.packet(count=2))
        self.assertIsNone(self.source.exchange(None))
        self.assertIsNone(self.source.exchange({**request, 'outcome': 'accepted', 'x': 0}))
        self.assertIsNone(self.source.exchange({**request, 'outcome': 'accepted', 'reason': 'not_allowed'}))
        self.assertIsNone(self.source.exchange({**request, 'outcome': 'refused', 'reason': 'ship_reserved'}))
        expected = ['RESULT', request['order_id'], 'refused', 'ship_reserved']
        self.assertEqual(self.receive(), expected)
        self.send(self.packet())
        self.assertIsNone(self.source.exchange(None))
        self.assertEqual(self.receive(), expected)

        # Skipped serial 2 represents a lookup that failed before sending a datagram.
        self.send(self.packet(count=3))
        subsequent = self.source.exchange(None)
        self.assertNotEqual(request['order_id'], subsequent['order_id'])
        self.send(self.packet())
        self.assertIsNone(self.source.exchange({**request, 'outcome': 'accepted'}))
        self.assertIsNone(self.source.exchange({**subsequent, 'outcome': 'unknown'}))
        self.assertEqual(self.receive(), ['RESULT', subsequent['order_id'], 'unknown'])

    def test_rejects_unowned_wrong_lease_out_of_bounds_and_nonfinite(self):
        stranger = self.local_socket()
        for invalid in (b'', self.packet(lease='0' * 32), self.packet(count=0), self.packet(index='0'),
                        self.packet(x='nan'), self.packet(x='inf'), self.packet(x='10000000.1'),
                        self.packet(index='1234567'), self.packet(x='2' * 600)):
            self.send(invalid)
        self.send(self.packet(mode='replace'))
        self.send(self.packet(), stranger)
        self.assertIsNone(self.source.exchange(None))
        self.send(self.packet())
        self.assertEqual(self.source.exchange(None)['ship_code'], 'WRN-302')

    def test_v2_requires_and_binds_the_exact_queue_mode(self):
        with self.assertRaises(ValueError):
            CompanyFleetSource(version=True)
        source = CompanyFleetSource(version=2)
        self.addCleanup(source.close)
        self.config = source.configure('0' * 32)
        self.assertEqual(self.config['version'], 2)
        self.lease = self.config['lease']
        self.source = source
        source.bind_validator(lambda peer: peer == self.validator)
        self.send(self.packet())
        self.send(self.packet(mode='priority'))
        self.assertIsNone(source.exchange(None))
        self.send(self.packet(mode='replace'))
        request = source.exchange(None)
        self.assertEqual(request['queue_mode'], 'replace')
        self.send(self.packet(mode='append'))
        self.assertIsNone(source.exchange(dict(request, outcome='accepted', queue_mode='append')))
        legacy_receipt = {key: value for key, value in request.items() if key != 'queue_mode'}
        self.assertIsNone(source.exchange(dict(legacy_receipt, outcome='accepted')))
        self.assertIsNone(source.exchange(dict(request, outcome='accepted')))
        self.assertEqual(self.receive(), ['RESULT', request['order_id'], 'accepted'])
        self.send(self.packet(count=2, mode='append'))
        request = source.exchange(None)
        self.assertEqual(request['queue_mode'], 'append')

    @unittest.skipUnless(shutil.which('luajit'), 'LuaJIT required')
    def test_regular_stock_context_capture_and_bound_results(self):
        for version in ('1', '2'):
            with self.subTest(version=version):
                subprocess.run(['luajit', 'tests/authority_company_fleet_capture.lua', version],
                               cwd=ROOT, timeout=10, check=True)
