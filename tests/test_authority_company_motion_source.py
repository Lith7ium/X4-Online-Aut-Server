"""Loopback IPC and real Lua sampler with synthetic selected-seat observations."""
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from authority.client_seat_overlay import company_motion_md, company_motion_ui
from authority.company_motion_frames import MAX_COUNTER
from authority.company_motion_reconnect import ReconnectingCompanyMotionClient
from authority.company_motion_observer import MAX_OBSERVER_DATAGRAM
from authority.company_motion_source import CompanyMotionSource
from authority.company_npc_measurement import NPC_CODE, NPC_INDEX, PILOT_INDEX, RADAR_ENDED, REGISTRY_EPOCH, SECTOR


class CompanyMotionSourceTest(unittest.TestCase):
    def test_local_npc_measurement_opt_in_requires_pinned_read_only_seat(self):
        target = (NPC_INDEX, NPC_CODE)
        operational = '1' * PILOT_INDEX
        defaults = CompanyMotionSource(observed_npc=target)
        self.addCleanup(defaults.close)
        self.assertFalse(defaults.registered_npc_local_measurement)
        self.assertNotIn('registered_npc_local_measurement',
                         defaults.configure(REGISTRY_EPOCH, PILOT_INDEX, operational_bits=operational))
        for options in ({}, {'observed_npc': (NPC_INDEX, 'ABC-123')},
                        {'observed_npc': target, 'registered_npc_hold_trial': True},
                        {'observed_npc': target, 'registered_npc_hold_trial': True,
                         'registered_npc_alignment_trial': True}):
            with self.assertRaisesRegex(ValueError, 'read-only observation'):
                CompanyMotionSource(registered_npc_local_measurement=True, **options)
        source = CompanyMotionSource(observed_npc=target, registered_npc_local_measurement=True)
        self.addCleanup(source.close)
        for epoch, index in (('b' * 32, PILOT_INDEX), (REGISTRY_EPOCH, PILOT_INDEX - 1)):
            with self.assertRaisesRegex(ValueError, 'verified HVC-420'):
                source.configure(epoch, index, operational_bits=operational)
        settings = source.configure(REGISTRY_EPOCH, PILOT_INDEX, operational_bits=operational)
        self.assertTrue(source.registered_npc_local_measurement)
        self.assertTrue(settings['registered_npc_local_measurement'])
        self.assertIsNone(source.take_npc_measurement())

    def test_owned_udp_measurement_is_latest_only_and_ends_once_beside_own_motion(self):
        source = CompanyMotionSource(observed_npc=(NPC_INDEX, NPC_CODE), registered_npc_local_measurement=True)
        self.addCleanup(source.close)
        settings = source.configure(REGISTRY_EPOCH, PILOT_INDEX, operational_bits='1' * PILOT_INDEX)
        destination = ('127.0.0.1', settings['port'])
        prefix = source._prefix()

        def point(sequence, capture, *, actor='0xabc', code=NPC_CODE, game=12, sector=SECTOR):
            return f'NPC_POINT|{NPC_INDEX}|{code}|{sector}|{actor}|{sequence}|visible|{capture}|{game}|1|2|3|4|5|6'

        def own(sequence):
            return f'SAMPLE3|{REGISTRY_EPOCH}|{PILOT_INDEX}|{sequence}|{SECTOR}|20|{sequence}|1|2|3|0|0|0|'

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as owned, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as foreign, \
                patch('authority.company_motion_source._npc_now', return_value=100) as clock:
            owned.bind(('127.0.0.1', 0))
            validate = Mock(side_effect=lambda address: address == owned.getsockname())
            observer = source.observe(validate)
            observer.bind_views(Mock())

            def send(*rows):
                for row in rows:
                    owned.sendto(prefix + row.encode('ascii'), destination)

            foreign.sendto(prefix + point(9, 99.9).encode('ascii'), destination)
            owned.sendto(b'C|' + b'f' * 32 + b'|' + settings['generation'].encode() + b'|'
                         + point(8, 99.8).encode('ascii'), destination)
            owned.sendto(b'C|' + settings['lease'].encode() + b'|' + b'f' * 32 + b'|'
                         + point(7, 99.7).encode('ascii'), destination)
            # The untrusted map must not convert a point into radar telemetry.
            send(f'VIEW|map|1|{REGISTRY_EPOCH}|{SECTOR}|{NPC_INDEX}|0xabc|{NPC_CODE}',
                 point(6, 99.6), own(1))
            self.assertEqual(source.poll(validate)['sequence'], 1)
            self.assertIsNone(source.take_npc_measurement())
            self.assertIsNone(observer.cockpit_binding())
            self.assertEqual(validate.call_count, 2)

            radar = f'VIEW|cockpit|1|{REGISTRY_EPOCH}|{SECTOR}|{NPC_INDEX}|0xabc|{NPC_CODE}'
            send(point(1, 99.1), radar, own(2))
            self.assertEqual(source.poll(validate)['sequence'], 2)
            self.assertEqual(observer.cockpit_binding(), (SECTOR, NPC_INDEX, '0xabc', NPC_CODE))
            self.assertEqual(source.take_npc_measurement()['sequence'], 1)
            send(radar, point(2, 99.15), point(3, 99.2), point(3, 99.3), point(4, 99.1))
            self.assertIsNone(source.poll(validate))
            measurement = source.take_npc_measurement()
            self.assertEqual((measurement['index'], measurement['sequence'], measurement['actor']),
                             (NPC_INDEX, 3, '0xabc'))
            self.assertEqual(measurement['velocity_m_per_game_second'], [4., 5., 6.])
            self.assertIsNone(source.take_npc_measurement())
            measurement['sequence'] = 0  # The caller cannot reset the producer's progress.

            send(point(3, 99.3), point(4, 99.4, actor='0xabd'), point(4, 99.5, game=11),
                 point(5, 98), point(5, 101), point(5, 99.7, code='ABC-123'),
                 point(5, 99.7, sector='sector_other'),
                 point(5, 'NaN'), point(5, 99.7).replace('|1|2|3|', '|1_0|2|3|'),
                 point(5, 99.7) + '|' + 'Z' * 600, own(3))
            owned.sendto(prefix + b'NPC_POINT|\xff', destination)
            self.assertEqual(source.poll(validate)['sequence'], 3)
            self.assertIsNone(source.take_npc_measurement())

            send(point(4, 99.7), 'GAP', point(5, 99.8))
            self.assertIsNone(source.poll(validate))
            self.assertIsNone(source.take_npc_measurement())
            send(own(4), point(5, 99.8))
            self.assertEqual(source.poll(validate)['sequence'], 4)
            errors = []

            def read_from_wrong_thread():
                try:
                    source.take_npc_measurement()
                except RuntimeError as error:
                    errors.append(str(error))

            thread = threading.Thread(target=read_from_wrong_thread)
            thread.start()
            thread.join(timeout=1)
            self.assertEqual(errors, ['exclusive company motion owner thread required'])
            self.assertEqual(source.take_npc_measurement()['sequence'], 5)

            send(point(6, 99.9))
            self.assertIsNone(source.poll(validate))
            clock.return_value = 101
            self.assertIsNone(source.take_npc_measurement())
            clock.return_value = 100
            send(point(6, 100))
            self.assertIsNone(source.poll(validate))
            self.assertIsNone(source.take_npc_measurement())

            send(point(7, 100), f'VIEW|cockpit|2|{REGISTRY_EPOCH}|-|0|-|-',
                 f'NPC_END|{NPC_INDEX}|ABC-123')
            self.assertIsNone(source.poll(validate))
            self.assertIsNone(source.take_npc_measurement())  # Map retains its view.
            self.assertIsNone(observer.cockpit_binding())
            send(f'NPC_END|{NPC_INDEX}|{NPC_CODE}', own(5), f'NPC_END|{NPC_INDEX}|{NPC_CODE}', point(8, 100))
            self.assertEqual(source.poll(validate)['sequence'], 5)
            self.assertIs(source.take_npc_measurement(), RADAR_ENDED)
            self.assertIsNone(source.take_npc_measurement())
            send(f'VIEW|cockpit|3|{REGISTRY_EPOCH}|{SECTOR}|{NPC_INDEX}|0xabc|{NPC_CODE}', point(9, 100))
            self.assertIsNone(source.poll(validate))
            self.assertIsNone(source.take_npc_measurement())

    def test_local_npc_datagram_limit_and_measurement_without_observer(self):
        source = CompanyMotionSource(observed_npc=(NPC_INDEX, NPC_CODE), registered_npc_local_measurement=True)
        self.addCleanup(source.close)
        settings = source.configure(REGISTRY_EPOCH, PILOT_INDEX, operational_bits='1' * PILOT_INDEX)
        destination = ('127.0.0.1', settings['port'])
        prefix = source._prefix()
        point = f'NPC_POINT|{NPC_INDEX}|{NPC_CODE}|{SECTOR}|0xabc|1|visible|99.9|12|1|2|3|4|5|6'.encode()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as owned, \
                patch('authority.company_motion_source._npc_now', return_value=100):
            owned.bind(('127.0.0.1', 0))
            validate = lambda address: address == owned.getsockname()
            owned.sendto(prefix + point, destination)
            owned.sendto(prefix + f'NPC_END|{NPC_INDEX}|{NPC_CODE}'.encode(), destination)
            self.assertIsNone(source.poll(validate))
            self.assertIsNone(source.take_npc_measurement())

            at_limit = (prefix + point).ljust(MAX_OBSERVER_DATAGRAM, b'0')
            with patch.object(source, '_receive_npc_measurement', wraps=source._receive_npc_measurement) as receive:
                owned.sendto(at_limit, destination)
                owned.sendto(at_limit + b'0', destination)
                self.assertIsNone(source.poll(validate))
                receive.assert_called_once_with(at_limit[len(prefix):])

            def owned_sample(sequence, size):
                header = prefix + f'SAMPLE3|{REGISTRY_EPOCH}|{PILOT_INDEX}|{sequence}|{SECTOR}|'.encode()
                body = f'20|{sequence}|1|2|3|0|0|0|'.encode()
                return header + b'0' * (size - len(header) - len(body)) + body

            owned.sendto(owned_sample(1, 512), destination)
            self.assertEqual(source.poll(validate)['sequence'], 1)
            owned.sendto(owned_sample(2, 513), destination)
            with self.assertRaisesRegex(ValueError, 'invalid local company motion packet'):
                source.poll(validate)

    def test_hold_trial_is_explicit_and_bound_to_a_single_vanilla_pair(self):
        target = (943, 'MFQ-419')
        epoch = '26a1a365c66788aa06f0aeb9e18ff62e'
        operational = '1' * 1073
        observer = CompanyMotionSource(observed_npc=target)
        self.addCleanup(observer.close)
        self.assertNotIn('registered_npc_hold_trial', observer.configure(epoch, 1073, operational_bits=operational))
        self.assertNotIn('authority_saved_replica_control.xml', company_motion_md(observer))
        self.assertNotIn('saved_replica_control.lua', company_motion_ui(observer))
        with self.assertRaisesRegex(ValueError, 'requires observation'):
            CompanyMotionSource(observed_npc=(943, 'NPC-042'), registered_npc_hold_trial=True)
        trial = CompanyMotionSource(observed_npc=target, registered_npc_hold_trial=True)
        self.addCleanup(trial.close)
        for world, pilot in ((epoch, 1072), ('b' * 32, 1073)):
            with self.assertRaisesRegex(ValueError, 'verified HVC-420'):
                trial.configure(world, pilot, operational_bits=operational)
        self.assertTrue(trial.configure(epoch, 1073, operational_bits=operational)['registered_npc_hold_trial'])
        self.assertIn('authority_saved_replica_control.xml', company_motion_md(trial))
        self.assertIn('registered_npc_hold_trial.lua', company_motion_ui(trial))

    def test_alignment_adds_only_local_cockpit_receive_time_and_clears_track_on_loss(self):
        epoch, generation = '26a1a365c66788aa06f0aeb9e18ff62e', 'd' * 32
        source = CompanyMotionSource(observed_npc=(943, 'MFQ-419'), registered_npc_hold_trial=True,
                                     registered_npc_alignment_trial=True)
        self.addCleanup(source.close)
        config = source.configure(epoch, 1073, operational_bits='1' * 1073)
        self.assertTrue(config['registered_npc_alignment_trial'])
        link = ReconnectingCompanyMotionClient.observing(1, {'ticket': 'f' * 64}, source, lambda peer: True)
        self.addCleanup(link.close)
        observer = source._observer
        packets = []
        observer._send = lambda packet: packets.append(packet) is None
        for slot in ('cockpit', 'map'):
            observer.receive(['VIEW', slot, '1', epoch, 'sector_a', '943', '0xab', 'MFQ-419'])
        sample = dict(version=3, epoch=epoch, index=943, sequence=7, generation=generation, sector_macro='sector_a',
                      source_time=31.0, capture_time=113.0, x=1.0, y=2.0, z=3.0, real_velocity=None)
        with patch('authority.company_motion_observer.time.monotonic', return_value=29.0):
            observer.snapshot(sample)
        rows = {packet.split('|')[1]: packet.split('|') for packet in packets if packet.startswith('REMOTE|')}
        self.assertEqual((len(rows['cockpit']), rows['cockpit'][-2:]), (17, ['113', '29']))
        self.assertEqual((len(rows['map']), rows['map'][-1]), (16, '113'))
        link._on_transport_lost(943, generation)
        self.assertTrue(any(packet.startswith('END|cockpit|') for packet in packets))
        self.assertIsNone(observer.resume_route())

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

    def test_first_local_motion_stages_require_matching_datagram_owned_peer_and_valid_pose(self):
        stages = []
        source = CompanyMotionSource(on_stage=stages.append)
        self.addCleanup(source.close)
        epoch = 'a' * 32
        config = source.configure(epoch, 1961)
        destination = ('127.0.0.1', config['port'])
        prefix = source._prefix()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as owned, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as foreign:
            owned.bind(('127.0.0.1', 0))
            validate = Mock(side_effect=lambda peer: peer == owned.getsockname())
            receive = Mock()
            receive.recvfrom.side_effect = [(prefix + b'GAP', ('192.0.2.1', 7)),
                                            (b'wrong-prefix|GAP', owned.getsockname()),
                                            BlockingIOError]
            with patch.object(source, '_socket', receive):
                self.assertIsNone(source.poll(validate))
            validate.assert_not_called()
            self.assertEqual(stages, [])

            foreign.sendto(prefix + b'GAP', destination)
            self.assertIsNone(source.poll(validate))
            self.assertEqual(stages, ['first_local_motion_datagram_matched'])

            owned.sendto(prefix + b'GAP', destination)
            self.assertIsNone(source.poll(validate))
            self.assertEqual(stages[-1], 'first_local_motion_peer_verified')
            owned.sendto(prefix + f'SAMPLE3|{epoch}|1960|1|sector_a|20|1|1|2|3|-|-|-|'.encode(),
                         destination)
            with self.assertRaisesRegex(ValueError, 'foreign local'):
                source.poll(validate)
            self.assertEqual(len(stages), 2)

            for sequence in (1, 2):
                owned.sendto(prefix + f'SAMPLE3|{epoch}|1961|{sequence}|sector_a|20|{sequence}|1|2|3|-|-|-|'.encode(),
                             destination)
                self.assertEqual(source.poll(validate)['sequence'], sequence)
            self.assertEqual(stages, ['first_local_motion_datagram_matched',
                                      'first_local_motion_peer_verified',
                                      'first_local_own_pose_validated'])

    def test_selected_sampler_only_captures_after_resume_and_resets_rates(self):
        luajit = shutil.which('luajit')
        runner = [luajit] if luajit else [sys.executable, 'tests/run_fixture_lua.py']
        subprocess.run([*runner, 'tests/authority_company_motion_source.lua'], cwd=ROOT, check=True)

    def test_auto_cockpit_roster_retries_on_the_owned_udp_peer_without_subscribing(self):
        source = CompanyMotionSource(cockpit_radar_playback=True, cockpit_max_pilots=1)
        self.addCleanup(source.close)
        epoch = 'a' * 32
        config = source.configure(epoch, 1961)
        destination = ('127.0.0.1', config['port'])
        prefix = source._prefix()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as owned, \
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as foreign, \
                patch('authority.company_motion_observer.time.monotonic', return_value=10) as clock:
            owned.bind(('127.0.0.1', 0))
            owned.settimeout(.1)
            foreign.setblocking(False)
            validate = lambda peer: peer == owned.getsockname()
            observer = source.observe(validate)
            set_views = Mock()
            observer.bind_views(set_views)
            observer.set_pilot_roster((12, 7354))
            foreign.sendto(prefix + b'GAP', destination)
            clock.return_value = 10.5
            source.poll(validate)
            with self.assertRaises(BlockingIOError):
                foreign.recv(576)
            owned.sendto(prefix + b'GAP', destination)
            source.poll(validate)
            clock.return_value = 11
            source.poll(validate)
            expected = prefix + f'ROSTER|{epoch}|1|12|7354'.encode()
            self.assertEqual(owned.recv(576), expected)

            observer.set_pilot_roster((12, 7354))
            for malformed in ((7354, 12), (12, 12), (1961,), (200001,), (True,), (1, 2, 3, 4)):
                observer.set_pilot_roster(malformed)
            source.poll(validate)
            with self.assertRaises(socket.timeout):
                owned.recv(576)
            clock.return_value = 11.5
            source.poll(validate)
            self.assertEqual(owned.recv(576), expected)
            observer.set_pilot_roster(())
            self.assertEqual(owned.recv(576), prefix + f'ROSTER|{epoch}|2'.encode())
            set_views.assert_not_called()


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
        self.observer.set_pilot_roster((7354,))
        self.validator_live = False
        self.observer.snapshot(self.sample(1))
        with self.assertRaises(socket.timeout):
            self.remote()
        self.validator_live = True
        self.send(f'SAMPLE3|{self.epoch}|1961|2|sector_a|20|2|2|2|3|10|0|0|')
        self.assertEqual(self.poll()['sequence'], 2)


if __name__ == '__main__':
    unittest.main()
