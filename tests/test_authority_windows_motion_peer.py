"""Windows UDP ownership table contract; no Windows APIs or X4 are started."""
import ctypes
from pathlib import Path
import socket
import struct
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import authority.windows_motion_peer as subject


def _table4(rows):
    offset = subject._Udp4Table.rows.offset
    raw = bytearray(offset + len(rows) * ctypes.sizeof(subject._Udp4Row))
    struct.pack_into('<I', raw, 0, len(rows))
    for index, (address, port, pid) in enumerate(rows):
        row = offset + index * ctypes.sizeof(subject._Udp4Row)
        raw[row:row + 4] = socket.inet_aton(address)
        struct.pack_into('<I', raw, row + 4, socket.htons(port))
        struct.pack_into('<I', raw, row + 8, pid)
    return bytes(raw)


def _table6(rows):
    offset = subject._Udp6Table.rows.offset
    raw = bytearray(offset + len(rows) * ctypes.sizeof(subject._Udp6Row))
    struct.pack_into('<I', raw, 0, len(rows))
    for index, (address, port, pid) in enumerate(rows):
        row = offset + index * ctypes.sizeof(subject._Udp6Row)
        raw[row:row + 16] = socket.inet_pton(socket.AF_INET6, address)
        struct.pack_into('<I', raw, row + 16, 0)
        struct.pack_into('<I', raw, row + 20, socket.htons(port))
        struct.pack_into('<I', raw, row + 24, pid)
    return bytes(raw)


class FakeOwner(subject.WindowsX4Process):
    def __init__(self, outcomes=None):
        self.pid = 1234
        self.outcomes = list(outcomes or [True])
        self.calls = []

    def owns_running_pid(self, pid):
        self.calls.append(pid)
        if len(self.outcomes) > 1:
            return self.outcomes.pop(0)
        return self.outcomes[0]


class FakeApiFunction:
    def __init__(self, raw, *, result=0, grow=0, initial_size=None):
        self.raw = raw
        self.result = result
        self.grow = grow
        self.initial_size = initial_size
        self.calls = 0
        self.buffer_calls = 0
        self.restype = None
        self.argtypes = None

    def __call__(self, buffer, size_pointer, order, family, table_class, reserved):
        self.calls += 1
        size = size_pointer._obj
        if buffer is None:
            size.value = self.initial_size if self.initial_size is not None else len(self.raw)
            return subject._ERROR_INSUFFICIENT_BUFFER
        self.buffer_calls += 1
        if self.result:
            return self.result
        if self.buffer_calls <= self.grow:
            size.value += 8
            return subject._ERROR_INSUFFICIENT_BUFFER
        ctypes.memmove(buffer, self.raw, len(self.raw))
        size.value = len(self.raw)
        return 0


class WindowsMotionPeerTests(unittest.TestCase):
    def setUp(self):
        self.owner = FakeOwner()
        self.destination = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.destination.bind(('127.0.0.1', 0))
        self.addCleanup(self.destination.close)
        self.subject = subject.WindowsMotionPeer(self.owner, self.destination)
        self.peer = ('127.0.0.1', 45123)
        self.rows4 = [('127.0.0.1', self.peer[1], self.owner.pid)]
        self.rows6 = []

    def validate(self):
        with patch.object(subject, '_windows_runtime', return_value=True), \
             patch.object(subject, '_load_udp_table_function', return_value=object()), \
             patch.object(subject, '_get_extended_udp_table', side_effect=(self.rows4, self.rows6)):
            return self.subject(self.peer)

    def test_windows_row_layout_and_network_byte_order_decode(self):
        self.assertEqual(ctypes.sizeof(subject._Udp4Row), 12)
        self.assertEqual(ctypes.sizeof(subject._Udp6Row), 28)
        parsed4 = subject._parse_table(_table4([('127.0.0.1', 45123, 1234)]), subject._AF_INET)
        parsed6 = subject._parse_table(_table6([('::1', 45124, 5678)]), subject._AF_INET6)
        self.assertEqual(parsed4, [('127.0.0.1', 45123, 1234)])
        self.assertEqual(parsed6, [('::1', 45124, 5678)])

    def test_api_unused_tail_is_accepted(self):
        raw = _table4([('127.0.0.1', 45123, 1234)]) + b'padding'
        self.assertEqual(subject._parse_table(raw, subject._AF_INET),
                         [('127.0.0.1', 45123, 1234)])

    def test_exact_peer_pid_is_accepted(self):
        self.assertTrue(self.validate())
        self.assertEqual(self.owner.calls, [1234, 1234])

    def test_empty_table_foreign_pid_wrong_address_and_duplicate_are_refused(self):
        for rows in ([], [('127.0.0.1', 45123, 9999)],
                     [('192.0.2.7', 45123, 1234)], [('127.0.0.2', 45123, 1234)],
                     [('127.0.0.1', 45123, 1234), ('127.0.0.1', 45123, 1234)]):
            with self.subTest(rows=rows):
                self.rows4 = rows
                self.assertFalse(self.validate())

    def test_implicitly_bound_wildcard_owner_row_is_accepted(self):
        # The shipped network DLL opens a nonblocking, implicitly bound Winsock
        # UDP socket. Native Windows lists that sender as 0.0.0.0, not 127.0.0.1.
        self.rows4 = [('0.0.0.0', self.peer[1], self.owner.pid)]
        self.assertTrue(self.validate())

    def test_wildcard_row_keeps_pid_and_single_port_checks(self):
        for rows4, rows6 in (([('0.0.0.0', 45123, 9999)], []),
                             ([('0.0.0.0', 45123, 1234), ('127.0.0.1', 45123, 1234)], []),
                             ([('0.0.0.0', 45123, 1234), ('127.0.0.1', 45123, 9999)], []),
                             ([('0.0.0.0', 45123, 1234)], [('::', 45123, 1234)])):
            with self.subTest(rows4=rows4, rows6=rows6):
                self.rows4, self.rows6 = rows4, rows6
                self.assertFalse(self.validate())

    @unittest.skipUnless(sys.platform == 'win32', 'native Windows UDP owner table')
    def test_native_implicitly_bound_sender_is_accepted(self):
        # Reproduces the Windows pilot path without X4: this process stands in
        # for the owner, and its implicitly bound sender reaches the destination.
        owner = FakeOwner()
        owner.pid = subject.os.getpid()
        peer_check = subject.WindowsMotionPeer(owner, self.destination)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sender:
            sender.setblocking(False)
            sender.connect(self.destination.getsockname())
            sender.send(b'C|probe')
            self.destination.settimeout(2)
            data, peer = self.destination.recvfrom(64)
            self.assertEqual(data, b'C|probe')
            self.assertTrue(peer_check(peer))

    def test_ipv6_same_source_port_competitor_is_refused(self):
        self.rows6 = [('::1', self.peer[1], 4321)]
        self.assertFalse(self.validate())

    def test_owner_is_checked_before_and_after_snapshot(self):
        for outcomes in ([False], [True, False]):
            self.owner = FakeOwner(outcomes)
            self.subject = subject.WindowsMotionPeer(self.owner, self.destination)
            self.assertFalse(self.validate())

    def test_destination_closed_or_peer_not_loopback_is_refused(self):
        self.destination.close()
        with patch.object(subject, '_windows_runtime', return_value=True):
            self.assertFalse(self.subject(self.peer))
        self.destination = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.destination.bind(('127.0.0.1', 0))
        self.addCleanup(self.destination.close)
        self.subject = subject.WindowsMotionPeer(self.owner, self.destination)
        with patch.object(subject, '_windows_runtime', return_value=True):
            self.assertFalse(self.subject(('127.0.0.2', self.peer[1])))

    def test_off_windows_call_is_refused(self):
        with patch.object(subject, '_windows_runtime', return_value=False):
            self.assertFalse(self.subject(self.peer))

    def test_parser_rejects_empty_malformed_truncated_and_oversize_counts(self):
        for raw, family in ((b'', subject._AF_INET), (b'bad!', subject._AF_INET),
                            (struct.pack('<I', 1), subject._AF_INET),
                            (struct.pack('<I', 0xffffffff), subject._AF_INET6)):
            with self.subTest(raw=raw, family=family), self.assertRaises(ValueError):
                subject._parse_table(raw, family)

    def test_parser_rejects_oversize_buffer_without_large_allocation(self):
        raw = b'\0\0\0\0' + b'x' * 8
        with patch.object(subject, '_MAX_TABLE_BYTES', 8), self.assertRaises(ValueError):
            subject._parse_table(raw, subject._AF_INET)

    def test_api_sizing_retry_success_and_errors(self):
        raw = _table4([('127.0.0.1', 45123, 1234)])
        function = FakeApiFunction(raw, grow=1)
        self.assertEqual(subject._get_extended_udp_table(subject._AF_INET, function),
                         [('127.0.0.1', 45123, 1234)])
        self.assertEqual(function.calls, 3)

        for function in (FakeApiFunction(raw, result=5),
                         FakeApiFunction(raw, initial_size=subject._MAX_TABLE_BYTES + 1),
                         FakeApiFunction(raw, grow=3)):
            with self.subTest(function=function), self.assertRaises(OSError):
                subject._get_extended_udp_table(subject._AF_INET, function)

    def test_api_function_is_loaded_once_and_tables_remain_fresh(self):
        api = object()
        with patch.object(subject, '_windows_runtime', return_value=True), \
             patch.object(subject, '_load_udp_table_function', return_value=api) as load, \
             patch.object(subject, '_get_extended_udp_table', side_effect=(self.rows4, self.rows6,
                                                                          self.rows4, self.rows6)) as query:
            self.assertTrue(self.subject(self.peer))
            self.assertTrue(self.subject(self.peer))
        load.assert_called_once_with()
        self.assertEqual([call.args for call in query.call_args_list], [
            (subject._AF_INET, api), (subject._AF_INET6, api),
            (subject._AF_INET, api), (subject._AF_INET6, api)])

    def test_query_failure_malformed_table_and_changing_owner_fail_closed(self):
        with patch.object(subject, '_windows_runtime', return_value=True), \
             patch.object(subject, '_load_udp_table_function', return_value=object()), \
             patch.object(subject, '_get_extended_udp_table', side_effect=OSError('table failed')):
            self.assertFalse(self.subject(self.peer))
        with patch.object(subject, '_windows_runtime', return_value=True), \
             patch.object(subject, '_load_udp_table_function', return_value=object()), \
             patch.object(subject, '_get_extended_udp_table', side_effect=(ValueError('bad table'), [])):
            self.assertFalse(self.subject(self.peer))
        self.owner = FakeOwner([True, False])
        self.subject = subject.WindowsMotionPeer(self.owner, self.destination)
        self.assertFalse(self.validate())

    def test_malformed_peer_shape_is_refused_and_interrupts_propagate(self):
        with patch.object(subject, '_windows_runtime', return_value=True):
            self.assertFalse(self.subject(('127.0.0.1', 45123, 9)))
        def interrupt(_pid):
            raise KeyboardInterrupt()
        self.owner.owns_running_pid = interrupt
        with patch.object(subject, '_windows_runtime', return_value=True), \
             self.assertRaises(KeyboardInterrupt):
            self.subject(self.peer)


if __name__ == '__main__':
    unittest.main()
