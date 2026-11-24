"""Loopback IPC and real Lua sampler with synthetic selected-seat observations."""
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from authority.company_motion_frames import MAX_COUNTER
from authority.company_motion_observer import MAX_OBSERVER_DATAGRAM
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


class CompanyMotionObserverTest(unittest.TestCase):
    def setUp(self):
        self.epoch, self.generation = 'a' * 32, 'b' * 32
        self.source = CompanyMotionSource()
        self.addCleanup(self.source.close)
        config = self.source.configure(self.epoch, 1961)
        self.destination = ('127.0.0.1', config['port'])
        self.prefix = f'C|{config["lease"]}|{config["generation"]}|'.encode('ascii')
        self.owned = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.foreign = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for peer in (self.owned, self.foreign):
            self.addCleanup(peer.close)
            peer.bind(('127.0.0.1', 0))
            peer.settimeout(.1)
        self.validator_live = True
        self.observer = self.source.observe(self.validate)
        self.views = []
        self.observer.bind_views(self.views.append)

    def validate(self, peer):
        return self.validator_live and peer == self.owned.getsockname()

    def send(self, body, *, peer=None):
        if type(body) is str:
            body = body.encode('ascii')
        (peer or self.owned).sendto(self.prefix + body, self.destination)

    def view(self, slot, serial, *, sector='sector_a', index=7354, actor='0xabcdef',
             code='WRN-302', peer=None):
        self.send(f'VIEW|{slot}|{serial}|{self.epoch}|{sector}|{index}|{actor}|{code}', peer=peer)

    def clear(self, slot, serial):
        self.send(f'VIEW|{slot}|{serial}|{self.epoch}|-|0|-|-')

    def poll(self):
        return self.source.poll(self.validate)

    def sample(self, sequence, *, velocity=None, sector='sector_a', index=7354):
        return {'version': 3, 'epoch': self.epoch, 'generation': self.generation,
                'index': index, 'sequence': sequence, 'sector_macro': sector, 'source_time': 1.0,
                'capture_time': sequence, 'x': 10.5, 'y': 11, 'z': -3, 'real_velocity': velocity}

    def remote(self):
        raw = self.owned.recv(MAX_OBSERVER_DATAGRAM + 1)
        self.assertTrue(raw.startswith(self.prefix))
        fields = raw[len(self.prefix):].decode('ascii').split('|')
        self.assertEqual((len(fields), fields[0]), (16, 'REMOTE'))
        return fields

    def seen(self, remote, *, actor='0xabcdef', code='WRN-302', peer=None):
        self.send('|'.join(('SEEN', *remote[1:8], actor, code, *remote[8:])), peer=peer)

    def test_simultaneous_views_deduplicate_subscription_but_confirm_each_slot(self):
        self.view('cockpit', 1)
        self.view('map', 5)
        self.poll()
        self.assertEqual(self.views, [[{'sector_macro': 'sector_a', 'indices': [7354]}]])
        stopped = {'x': -0.0, 'y': 0, 'z': 0}
        for sequence, rate in ((1, None), (2, stopped)):
            self.observer.snapshot(self.sample(sequence, velocity=rate))
            self.seen(self.remote())
            self.seen(self.remote())
            self.poll()
            self.assertTrue(all(slot['last_nonzero_velocity_confirmed'] is None
                                for slot in self.source.observer_stats()['slots'].values()))

        velocity = {'x': -2, 'y': 0, 'z': 4}
        self.observer.snapshot(self.sample(3, velocity=velocity))
        cockpit, map_view = self.remote(), self.remote()
        self.assertEqual(cockpit[1:4], ['cockpit', '1', self.epoch])
        self.assertEqual(map_view[1:4], ['map', '5', self.epoch])
        self.assertEqual(map_view[8:], ['10.5', '11', '-3', '-2', '0', '4', '1', '3'])
        self.seen(cockpit, peer=self.foreign)
        self.seen(map_view, actor='0x9999')
        self.seen([*map_view[:13], '999', *map_view[14:]])  # changed rate
        self.seen([*map_view[:-2], '999', map_view[-1]])  # changed game clock
        self.seen([*map_view[:-1], '999'])  # changed capture clock
        self.seen(map_view[:-2])  # previous, shorter packet format
        self.poll()
        stats = self.source.observer_stats()
        self.assertEqual(stats['samples_confirmed'], 4)
        self.assertTrue(all(slot['last_nonzero_velocity_confirmed'] is None for slot in stats['slots'].values()))
        self.seen(cockpit)
        self.seen(map_view)
        self.seen(cockpit)
        self.poll()
        stats = self.source.observer_stats()
        self.assertEqual(stats['samples_confirmed'], 6)
        self.assertEqual(stats['velocity_samples_confirmed'], 4)
        self.assertEqual(stats['last_confirmed']['slot'], 'map')
        self.assertEqual(stats['last_confirmed']['real_velocity'], velocity)
        self.assertEqual({name: slot['samples_confirmed'] for name, slot in stats['slots'].items()},
                         {'cockpit': 3, 'map': 3})
        self.assertNotIn(self.prefix.decode().split('|')[1], str(stats))
        self.assertNotIn(self.source.local_generation, str(stats))

        moving = {name: slot['last_confirmed'] for name, slot in stats['slots'].items()}
        self.observer.snapshot(self.sample(4, velocity=stopped))
        self.seen(self.remote())
        self.seen(self.remote())
        self.observer.snapshot(self.sample(5))
        self.seen(self.remote())
        self.seen(self.remote())
        self.seen(cockpit)  # Valid once, but too late to modify the current evidence.
        self.poll()
        stats = self.source.observer_stats()
        self.assertEqual((stats['samples_confirmed'], stats['velocity_samples_confirmed']), (10, 6))
        self.assertEqual(stats['last_confirmed']['sequence'], 5)
        for name, slot in stats['slots'].items():
            self.assertIsNone(slot['last_confirmed']['real_velocity'])
            self.assertEqual(slot['last_nonzero_velocity_confirmed'], moving[name])

    def test_heartbeat_expiry_old_serial_and_end_drop_pending_receipts(self):
        with patch('authority.company_motion_observer.time.monotonic') as clock:
            clock.return_value = 0
            self.view('map', 4)
            self.view('cockpit', 2, sector='sector_b', index=1970)
            self.poll()
            self.assertEqual(self.views[-1], [{'sector_macro': 'sector_a', 'indices': [7354]},
                                               {'sector_macro': 'sector_b', 'indices': [1970]}])
            clock.return_value = 2
            self.view('cockpit', 2, sector='sector_b', index=1970)
            self.view('map', 3, sector='sector_b', index=1970)
            self.view('map', 4, sector='sector_b', index=1970)
            self.poll()
            self.observer.snapshot(self.sample(1))
            delayed = self.remote()
            self.assertEqual(delayed[11:14], ['-', '-', '-'])
            clock.return_value = 3
            self.seen(delayed)
            self.poll()
            self.assertEqual(self.views[-1], [{'sector_macro': 'sector_b', 'indices': [1970]}])
            self.assertEqual(self.source.observer_stats()['samples_confirmed'], 0)
            self.observer.snapshot(self.sample(2, sector='sector_b', index=1970))
            active = self.remote()
            self.observer.end(1970, self.generation)
            self.seen(active)
            self.poll()
            self.assertEqual(self.source.observer_stats()['samples_confirmed'], 0)
            self.observer.snapshot(self.sample(3, sector='sector_b', index=1970))
            active = self.remote()
            self.clear('cockpit', 3)
            self.seen(active)
            self.poll()
            self.assertEqual(self.views[-1], [])
            self.assertEqual(self.source.observer_stats()['samples_confirmed'], 0)

    def test_maximum_formatted_observer_roundtrip_fits_its_datagram_limit(self):
        sector, index = 's' * 128, 200000
        actor = str(2**64 - 1)
        self.view('cockpit', MAX_COUNTER, sector=sector, index=index, actor=actor)
        self.poll()
        point = -1.2345678901234567e-300
        sample = self.sample(MAX_COUNTER, velocity=dict.fromkeys('xyz', point), sector=sector, index=index)
        sample.update(x=point, y=point, z=point, source_time=-point, capture_time=-point)
        self.observer.snapshot(sample)
        remote = self.remote()
        receipt_fields = ('SEEN', *remote[1:8], actor, 'WRN-302', *remote[8:])
        self.assertEqual(len(self.prefix) + len('|'.join(remote)), 516)
        self.assertEqual(len(self.prefix) + len('|'.join(receipt_fields)), 543)
        self.assertLessEqual(543, MAX_OBSERVER_DATAGRAM)
        self.seen(remote, actor=actor)
        self.poll()
        last = self.source.observer_stats()['last_confirmed']
        self.assertEqual(last['source_time'], sample['source_time'])
        self.assertEqual(last['capture_time'], sample['capture_time'])

    def test_bad_optional_datagrams_and_peer_loss_keep_cockpit_path_available(self):
        self.view('map', 1, peer=self.foreign)
        self.view('map', 1, index=1961)
        self.view('map', 9007199254740992)
        self.view('map', 1, actor='18446744073709551616')
        self.send(b'VIEW|map|1|' + b'x' * 600)
        self.send(b'VIEW|map|1|\xff')
        self.send('VIEW2|unknown observer version')
        self.send(f'SAMPLE3|{self.epoch}|1961|1|sector_a|20|1|1|2|3|-|-|-|')
        self.assertEqual(self.poll()['sequence'], 1)
        self.assertEqual(self.views, [])
        self.view('map', 1)
        self.poll()
        self.validator_live = False
        self.observer.snapshot(self.sample(1))
        with self.assertRaises(socket.timeout):
            self.remote()
        self.validator_live = True
        self.send(f'SAMPLE3|{self.epoch}|1961|2|sector_a|20|2|2|2|3|10|0|0|')
        self.assertEqual(self.poll()['sequence'], 2)


if __name__ == '__main__':
    unittest.main()
