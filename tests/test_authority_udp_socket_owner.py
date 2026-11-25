"""Real loopback ownership checks against separate local UDP processes."""
import errno
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/dev'))
import authority_tcp_job_client_live as ownership
from authority_tcp_job_client_live import socket_owner


COMPETITOR = r'''
import socket, struct, sys
family, local, local_port, destination_port, connected, pktinfo = sys.argv[1:]
family = socket.AF_INET6 if family == 'ipv6' else socket.AF_INET
peer = socket.socket(family, socket.SOCK_DGRAM)
peer.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
if family == socket.AF_INET6:
    peer.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
peer.bind((local, int(local_port)))
destination = ('::ffff:127.0.0.1' if family == socket.AF_INET6 else '127.0.0.1', int(destination_port))
if connected == 'yes':
    # A different remote still permits UDP sendto to the selected destination.
    peer.connect((destination[0], 9))
if pktinfo == 'yes':
    source = socket.inet_aton('127.0.0.1')
    peer.sendmsg([b'competing'], [(socket.IPPROTO_IP, 8, struct.pack('I4s4s', 0, source, b'\0'*4))], 0, destination)
else:
    peer.sendto(b'competing', destination)
print('ready', flush=True)
sys.stdin.readline()
'''


def process_identity():
    proc = Path('/proc/self')
    image = (proc / 'exe').stat()
    fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    return {'pid': os.getpid(), 'start_ticks': int(fields[19]),
            'exe_dev': image.st_dev, 'exe_ino': image.st_ino}


class UDPSocketOwnerTest(unittest.TestCase):
    def setUp(self):
        if sys.platform != 'linux' or not Path('/proc/self/net/udp6').is_file():
            self.skipTest('Linux IPv4/IPv6 UDP tables required')
        self.receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.receiver.close)
        self.addCleanup(self.peer.close)
        self.receiver.bind(('127.0.0.1', 0))
        self.receiver.settimeout(5)
        self.peer.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.peer.bind(('127.0.0.1', 0))
        self.peer.connect(self.receiver.getsockname())
        self.identity = process_identity()
        self.assertTrue(self.owner())

    def owner(self, diagnostic=None):
        return socket_owner(self.peer.getsockname(), self.receiver.getsockname(), self.identity,
                            diagnostic=diagnostic)

    def require_ipv6_mapped(self, local):
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as probe:
                probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
                probe.bind((local, 0))
                probe.connect(('::ffff:127.0.0.1', self.receiver.getsockname()[1]))
        except OSError as error:
            if error.errno in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL,
                               errno.EPROTONOSUPPORT, errno.ENOPROTOOPT,
                               errno.ENETUNREACH):
                self.skipTest(f'IPv4-mapped IPv6 is unavailable: {error}')
            raise

    def read_competitor_line(self, process):
        deadline = time.monotonic() + 5
        output = bytearray()
        while b'\n' not in output:
            remaining = deadline - time.monotonic()
            self.assertGreater(remaining, 0, 'competitor response timed out')
            readable, _, _ = select.select([process.stdout], [], [], remaining)
            self.assertTrue(readable, 'competitor response timed out')
            data = os.read(process.stdout.fileno(), 256)
            self.assertTrue(data, 'competitor closed its output before becoming ready')
            output.extend(data)
            self.assertLessEqual(len(output), 1024, 'competitor response too long')
        self.assertEqual(bytes(output), b'ready\n')

    def assert_competitor_rejected(self, family, local, *, connected=False, pktinfo=False):
        if family == 'ipv6':
            self.require_ipv6_mapped(local)
        command = [sys.executable, '-u', '-c', COMPETITOR, family, local,
                   str(self.peer.getsockname()[1]), str(self.receiver.getsockname()[1]),
                   'yes' if connected else 'no', 'yes' if pktinfo else 'no']
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        def stop():
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
        self.addCleanup(stop)
        self.read_competitor_line(process)
        self.peer.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        data, address = self.receiver.recvfrom(128)
        self.assertEqual(data, b'competing')
        self.assertEqual(address, self.peer.getsockname())
        reasons = []
        self.assertFalse(self.owner(diagnostic=reasons.append))
        expected = 'competing IPv6 source port' if family == 'ipv6' else 'competing IPv4 source port'
        self.assertEqual(reasons, [expected])

    def test_uncontested_connected_peer_is_owned(self):
        self.assertTrue(self.owner())

    def test_unconnected_wildcard_ipv4_sender_is_rejected(self):
        self.assert_competitor_rejected('ipv4', '0.0.0.0')

    def test_connected_ipv4_sender_with_different_remote_is_rejected(self):
        self.assert_competitor_rejected('ipv4', '127.0.0.1', connected=True)

    def test_ipv4_sender_on_different_address_using_pktinfo_is_rejected(self):
        self.assert_competitor_rejected('ipv4', '127.0.0.2', pktinfo=True)

    def test_unconnected_wildcard_ipv6_sender_is_rejected(self):
        self.assert_competitor_rejected('ipv6', '::')

    def test_connected_mapped_ipv6_sender_is_rejected(self):
        self.assert_competitor_rejected('ipv6', '::ffff:127.0.0.1', connected=True)

    def test_missing_connected_flow_has_a_static_diagnostic(self):
        reasons = []
        self.assertFalse(socket_owner(self.peer.getsockname(), self.peer.getsockname(),
                                      self.identity, diagnostic=reasons.append))
        self.assertEqual(reasons, ['connected IPv4 flow count differs from one (matches=0)'])

    def test_duplicate_flow_requires_one_fresh_owned_row_in_two_full_samples_at_most(self):
        original = ownership._udp_table
        peer_endpoint = ownership._proc_ipv4_endpoint(self.peer.getsockname())
        destination_endpoint = ownership._proc_ipv4_endpoint(self.receiver.getsockname())
        proc = Path('/proc') / str(self.identity['pid']) / 'net'
        ipv4, ipv6 = original(proc / 'udp', 8), original(proc / 'udp6', 32)
        row, = [item for item in ipv4 if item[1:3] == [peer_endpoint, destination_endpoint]]
        different_inode = row.copy()
        different_inode[9] = str(int(row[9]) + 1)
        changed_queue = row.copy()
        changed_queue[4] = '00000000:00000400' if row[4] != '00000000:00000400' else '00000000:00000000'
        changed_pointer = row.copy()
        changed_pointer[11] = '0000000000000001' if row[11] != '0000000000000001' else '0000000000000000'
        changed_remote = row.copy()
        changed_remote[2] = '0100007F:0001'
        contender = changed_remote.copy()
        contender[9] = different_inode[9]

        cases = (
            ('identical repeat', [ipv4 + [row.copy()], ipv4], True, None),
            ('different counters', [ipv4 + [changed_queue], ipv4], True, None),
            ('persistent duplicates', [ipv4 + [row.copy()], ipv4 + [row.copy()]], False,
             'connected IPv4 flow remained ambiguous on repeat sample (matches=2, distinct_inodes=1)'),
            ('different initial inodes', [ipv4 + [different_inode]], False,
             'connected IPv4 flow count differs from one (matches=2, distinct_inodes=2)'),
            ('second matching inode on repeat', [ipv4 + [row], ipv4 + [different_inode]], False,
             'connected IPv4 flow remained ambiguous on repeat sample (matches=2, distinct_inodes=2)'),
            ('replacement inode', [ipv4 + [row], [different_inode if item is row else item for item in ipv4]], False,
             'connected IPv4 flow identity changed on repeat sample'),
            ('different pointer', [ipv4 + [changed_pointer]], False, 'conflicting IPv4 rows for socket inode'),
            ('different remote', [ipv4 + [changed_remote]], False, 'conflicting IPv4 rows for socket inode'),
            ('initial competitor on another remote', [ipv4 + [row, contender]], False, 'competing IPv4 source port'),
            ('new competitor on repeat', [ipv4 + [row], ipv4 + [contender]], False, 'competing IPv4 source port'),
        )
        for label, samples, expected, reason in cases:
            with self.subTest(label=label):
                reasons = []
                reads = [table for sample in samples for table in (sample, ipv6)]
                with patch.object(ownership, '_udp_table', side_effect=reads) as table:
                    self.assertIs(self.owner(diagnostic=reasons.append), expected)
                self.assertEqual([call.args for call in table.call_args_list],
                                 [(proc / 'udp', 8), (proc / 'udp6', 32)] * len(samples))
                self.assertEqual(reasons, [reason] if reason else [])

    def test_duplicate_owned_descriptor_has_a_static_diagnostic(self):
        duplicate = os.dup(self.peer.fileno())
        self.addCleanup(os.close, duplicate)
        reasons = []
        self.assertFalse(self.owner(diagnostic=reasons.append))
        self.assertEqual(reasons, ['process socket descriptor count differs from one'])

    def test_missing_ipv6_table_fails_closed(self):
        original = Path.read_text
        def read_text(path, *args, **kwargs):
            if path.name == 'udp6':
                raise FileNotFoundError('simulated missing IPv6 table')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', read_text):
            with self.assertRaises(FileNotFoundError):
                self.owner()


if __name__ == '__main__':
    unittest.main()
