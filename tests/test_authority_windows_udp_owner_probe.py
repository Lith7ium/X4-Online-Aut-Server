"""Address category and privacy boundary tests without Windows or X4."""
import struct
import unittest

import authority_windows_udp_owner_probe as subject


def table(*rows):
    return struct.pack('<I', len(rows)) + b''.join(
        subject.ROW.pack(address, port.to_bytes(2, 'big'), pid)
        for address, port, pid in rows)


class WindowsUdpOwnerProbeTests(unittest.TestCase):
    def test_categories_for_the_observed_self_endpoint(self):
        loopback = b'\x7f\x00\x00\x01'
        wildcard = bytes(4)
        same = [(loopback, 41932, 277)]
        other = [(b'\x01\x02\x03\x04', 41932, 277)]
        wild = [(wildcard, 41932, 277)]
        foreign = [(wildcard, 41932, 278), (wildcard, 41933, 277)]
        for rows, wanted in ((same, 'loopback'), (wild, 'wildcard'), (other, 'other'),
                             (same + wild, 'multiple'), ([], 'missing')):
            with self.subTest(wanted=wanted):
                self.assertEqual(subject.own_row_category(table(*(rows + foreign)), 277, 41932), wanted)

    def test_rejects_truncation(self):
        with self.assertRaises(ValueError):
            subject.own_row_category(struct.pack('<I', 1), 277, 41932)
