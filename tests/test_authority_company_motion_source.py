"""Loopback IPC and real Lua sampler with synthetic selected-seat observations."""
from pathlib import Path
import socket
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from authority.company_motion_source import CompanyMotionSource


class CompanyMotionSourceTest(unittest.TestCase):
    def test_loopback_coalesces_and_refuses_foreign_peer_and_selection(self):
        source = CompanyMotionSource()
        self.addCleanup(source.close)
        epoch = 'a' * 32
        config = source.configure(epoch, 1961)
        self.assertEqual(source.registry, (epoch, 1961))
        self.assertEqual(source.local_generation, config['generation'])
        destination = ('127.0.0.1', config['port'])
        prefix = f'C|{config["lease"]}|{config["generation"]}|'
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as owned, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as foreign:
            owned.bind(('127.0.0.1', 0))
            validate = lambda peer: peer == owned.getsockname()
            payload = f'{prefix}SAMPLE3|{epoch}|1961|'
            foreign.sendto(f'{payload}100|sector_a|20|20|1|2|3|10|0|0|'.encode(), destination)
            self.assertIsNone(source.poll(validate))
            owned.sendto(f'{payload}1|sector_a|20|1|1|2|3|-|-|-|'.encode(), destination)
            owned.sendto(f'{payload}2|sector_a|20|2|2|2|3|10|0|0|'.encode(), destination)
            newest = source.poll(validate)
            self.assertEqual((newest['sequence'], newest['x'], newest['capture_time']), (2, 2, 2))
            self.assertIsNone(newest['real_velocity'])
            self.assertIsNone(source.poll(validate))
            owned.sendto(f'{payload}3|sector_a|20|3|3|2|3|10|0|0|'.encode(), destination)
            self.assertEqual(source.poll(validate)['real_velocity'], {'x': 10, 'y': 0, 'z': 0})
            owned.sendto(f'{payload}4|sector_b|20|4|4|2|3|-|-|-|'.encode(), destination)
            owned.sendto(f'{payload}5|sector_b|20|5|5|2|3|10|0|0|'.encode(), destination)
            newest = source.poll(validate)
            self.assertEqual(newest['sequence'], 5)
            self.assertIsNone(newest['real_velocity'])
            owned.sendto(f'{payload}6|sector_b|20|6|6|2|3|10|0|0|'.encode(), destination)
            owned.sendto(f'{prefix}GAP'.encode(), destination)
            self.assertIsNone(source.poll(validate))
            owned.sendto(f'{prefix}SAMPLE3|{epoch}|1960|7|sector_b|20|7|7|2|3|10|0|0|'.encode(), destination)
            with self.assertRaisesRegex(ValueError, 'foreign local'):
                source.poll(validate)

    def test_selected_sampler_only_captures_after_resume_and_resets_rates(self):
        subprocess.run(['luajit', 'tests/authority_company_motion_source.lua'], cwd=ROOT, check=True)


if __name__ == '__main__':
    unittest.main()
