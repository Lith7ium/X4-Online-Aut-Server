"""Exercise the private measurement independently of normal cockpit motion."""
import json
import socket
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from authority.company_motion_client import CompanyMotionClient
from authority.company_motion_reconnect import ReconnectingCompanyMotionClient
from authority.company_motion_service import CompanyMotionService
from authority.company_npc_measurement import (MEASUREMENT_KIND, NPC_INDEX, PILOT_INDEX,
                                              RADAR_ENDED, REGISTRY_EPOCH, SECTOR, NpcMeasurementIngress)


class NpcMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.rows = []
        self.ingress = NpcMeasurementIngress(lambda: True, self.rows.append)
        self.service = CompanyMotionService(npc_measurement=self.ingress)
        self.addCleanup(self.service.close)
        self.pilot = self.admit(1, PILOT_INDEX)
        self.other = self.admit(2, PILOT_INDEX + 1)
        self.service.set_running(True)

    def admit(self, handle, index):
        return self.service.admit(handle, f'{handle:032x}', index, SECTOR, 'a' * 32, REGISTRY_EPOCH)

    def open(self, grant):
        connection = socket.create_connection(('127.0.0.1', self.service.port), timeout=1)
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.addCleanup(connection.close)
        stream = connection.makefile('rb')
        self.addCleanup(stream.close)
        self.send(connection, {'kind': 'bind_motion', 'ticket': grant['ticket'], MEASUREMENT_KIND: True})
        return connection, stream

    def send(self, connection, message):
        connection.sendall(json.dumps(message).encode('ascii') + b'\n')
        self.service.poll()

    def view(self, connection, indices, sequence):
        self.send(connection, {'kind': 'motion_view', 'window': self.service.window, 'view_id': sequence,
                               'sector_macro': SECTOR, 'indices': indices})

    def sample(self, connection, sequence=1, **overrides):
        payload = {'epoch': REGISTRY_EPOCH, 'index': NPC_INDEX, 'code': 'MFQ-419', 'sector_macro': SECTOR,
                   'actor': '0x41961', 'sequence': sequence, 'attention': 'visible', 'position': [1., 2., 3.],
                   'game_time': float(sequence), 'capture_time': float(sequence),
                   'velocity_m_per_game_second': [100., 0., 0.]}
        self.send(connection, {'kind': MEASUREMENT_KIND, 'window': self.service.window, 'view_id': 1,
                               'generation': self.pilot['generation'], 'sample': payload, **overrides})

    def test_wrong_pilot_cannot_bind_probe(self):
        _, stream = self.open(self.other)
        self.assertEqual(stream.read(1), b'')
        self.assertEqual(self.rows, [])

    def test_first_measurement_requires_matching_view(self):
        connection, stream = self.open(self.pilot)
        self.assertTrue(json.loads(stream.readline())[MEASUREMENT_KIND])
        self.sample(connection)
        self.assertEqual(stream.read(1), b'')
        self.assertEqual(self.rows, [])

    def test_latest_only_and_view_ends_without_player_application(self):
        connection, stream = self.open(self.pilot)
        self.assertTrue(json.loads(stream.readline())[MEASUREMENT_KIND])
        self.view(connection, [NPC_INDEX], 1)
        self.sample(connection)
        self.sample(connection, sequence=3)
        self.assertEqual(self.ingress.latest, self.rows[-1]['sample'])
        self.assertEqual(self.rows[-1]['participant_handle'], 1)
        self.assertEqual(self.rows[-1]['sample']['velocity_m_per_game_second'], [100., 0., 0.])
        self.assertEqual(self.service.take_application_batch(), ())
        self.assertEqual(self.service.accepted_samples, 0)
        self.view(connection, [], 2)
        self.assertIsNone(self.ingress.latest)
        self.sample(connection, sequence=4, view_id=2)
        self.assertEqual(stream.read(1), b'')
        self.assertEqual(len(self.rows), 2)

    def test_expired_and_replayed_input_cannot_restore_probe(self):
        connection, stream = self.open(self.pilot)
        stream.readline()
        self.view(connection, [NPC_INDEX], 1)
        self.sample(connection)
        with patch('authority.company_motion_service.time.monotonic', return_value=self.ingress.deadline):
            self.sample(connection, sequence=2)
        self.assertEqual(stream.read(1), b'')
        self.assertIsNone(self.ingress.latest)
        self.assertEqual(len(self.rows), 1)

    def test_wrong_generation_is_not_accepted(self):
        connection, stream = self.open(self.pilot)
        stream.readline()
        self.view(connection, [NPC_INDEX], 1)
        self.sample(connection, generation=self.other['generation'])
        self.assertEqual(stream.read(1), b'')
        self.assertEqual(self.rows, [])

    def test_client_feeds_measurement_only_after_authenticated_view(self):
        current = {'epoch': REGISTRY_EPOCH, 'index': NPC_INDEX, 'code': 'MFQ-419', 'sector_macro': SECTOR,
                   'actor': '0x41961', 'sequence': 1, 'attention': 'inzone', 'position': [5., 6., 7.],
                   'game_time': 100., 'capture_time': time.monotonic(),
                   'velocity_m_per_game_second': [100., 2., 0.]}
        source = SimpleNamespace(registry=(REGISTRY_EPOCH, PILOT_INDEX), observed_npc=(NPC_INDEX, 'MFQ-419'),
                                 poll=lambda validate: None)
        client = CompanyMotionClient(self.service.port, self.pilot, source, lambda: True,
                                     on_snapshot=lambda sample: self.fail('measurement was forwarded'),
                                     on_end=lambda *_: None, npc_measurement_source=lambda: current)
        self.addCleanup(client.close)

        def drive():
            for _ in range(3):
                client.poll()
                self.service.poll()

        drive()
        self.assertEqual(self.rows, [])
        client.set_view(SECTOR, [NPC_INDEX])
        drive()
        self.assertEqual([row['sample'] for row in self.rows], [current.copy()])
        client.set_views([])
        current['sequence'] = 2
        drive()
        self.assertIsNone(self.ingress.latest)
        self.assertEqual(len(self.rows), 1)

    def test_client_transmits_only_fresh_native_qpc_measurements(self):
        source = SimpleNamespace(registry=(REGISTRY_EPOCH, PILOT_INDEX), observed_npc=(NPC_INDEX, 'MFQ-419'),
                                 poll=lambda validate: None)
        current = None
        client = CompanyMotionClient(self.service.port, self.pilot, source, lambda: True,
                                     on_snapshot=lambda _: self.fail('measurement was forwarded'),
                                     on_end=lambda *_: None, npc_measurement_source=lambda: current)
        self.addCleanup(client.close)

        def drive():
            for _ in range(4):
                client.poll()
                self.service.poll()

        client.set_view(SECTOR, [NPC_INDEX])
        drive()
        # Simulate GetTickCount64 rounding to 15.625 ms and a separate QPC origin.
        ticks = int(time.monotonic() * 64) / 64
        qpc = ticks + 0.750 + 0.008
        point = {'epoch': REGISTRY_EPOCH, 'index': NPC_INDEX, 'code': 'MFQ-419', 'sector_macro': SECTOR,
                 'actor': '0x41961', 'attention': 'inzone', 'position': [5., 6., 7.],
                 'game_time': 100., 'velocity_m_per_game_second': [100., 2., 0.]}
        with patch('authority.company_npc_measurement.sys.platform', 'win32'), \
                patch('authority.company_npc_measurement.time.monotonic', return_value=ticks), \
                patch('authority.company_npc_measurement.time.perf_counter', return_value=qpc):
            current = dict(point, sequence=1, capture_time=qpc - 0.002)
            drive()
            self.assertEqual([row['sample'] for row in self.rows], [current])

            # Stale to QPC but apparently fresh if compared with GetTickCount64.
            current = dict(point, sequence=2, capture_time=qpc - 1.002)
            drive()
            current = dict(point, sequence=3, capture_time=qpc + 0.002)
            drive()
            self.assertEqual([row['sample']['sequence'] for row in self.rows], [1])

    def test_pause_revokes_measurement_and_drops_delayed_window(self):
        connection, stream = self.open(self.pilot)
        stream.readline()
        self.view(connection, [NPC_INDEX], 1)
        self.sample(connection)
        original = self.service.window
        self.service.set_running(False)
        self.service.poll()
        self.assertIsNone(self.ingress.latest)
        stream.readline()
        self.sample(connection, sequence=2, window=original)
        self.assertEqual(stream.read(1), b'')
        self.assertEqual(len(self.rows), 1)

    def test_radar_end_retires_route_while_map_view_remains(self):
        local = None
        source = SimpleNamespace(registry=(REGISTRY_EPOCH, PILOT_INDEX), observed_npc=(NPC_INDEX, 'MFQ-419'),
                                 poll=lambda validate: None)
        client = CompanyMotionClient(self.service.port, self.pilot, source, lambda: True,
                                     on_snapshot=lambda sample: self.fail('measurement was forwarded'),
                                     on_end=lambda *_: None, npc_measurement_source=lambda: local)
        self.addCleanup(client.close)

        def drive():
            for _ in range(3):
                client.poll()
                self.service.poll()

        drive()
        client.set_view(SECTOR, [NPC_INDEX])  # Map only.
        drive()
        self.assertEqual(self.rows, [])
        local = {'epoch': REGISTRY_EPOCH, 'index': NPC_INDEX, 'code': 'MFQ-419', 'sector_macro': SECTOR,
                 'actor': '0x41961', 'sequence': 1, 'attention': 'visible', 'position': [1., 2., 3.],
                 'game_time': 10., 'capture_time': time.monotonic(),
                 'velocity_m_per_game_second': [2., 0., 1.]}
        drive()
        self.assertEqual(len(self.rows), 1)
        peer, = self.service._peers.values()
        map_views = peer.views.copy()
        local = RADAR_ENDED  # Radar lost; the map keeps this same combined view.
        drive()
        self.assertEqual(peer.views, map_views)
        self.assertEqual(self.ingress.retired_reason, 'radar_ended')
        self.assertIsNone(self.ingress.latest)
        self.sample(client._connection, sequence=2)
        self.assertEqual(len(self.rows), 1)
        self.assertEqual(client._connection.recv(1), b'')

    def test_private_ingress_uses_regular_reconnecting_observer_and_own_source(self):
        probe, own = None, None
        observer = SimpleNamespace(snapshot=lambda _: self.fail('NPC measurement was forwarded'),
                                   end=lambda *_: None)
        def save_view_callback(callback):
            observer.set_views = callback
        observer.bind_views = save_view_callback

        source = SimpleNamespace(
            registry=(REGISTRY_EPOCH, PILOT_INDEX), local_generation='b' * 32,
            observed_npc=(NPC_INDEX, 'MFQ-419'), registered_npc_hold_trial=False,
            registered_npc_local_measurement=True, take_npc_measurement=lambda: probe,
            poll=lambda validate: own, observe=lambda validate: observer)

        client = ReconnectingCompanyMotionClient.observing(self.service.port, self.pilot, source, lambda: True)
        self.addCleanup(client.close)
        def drive():
            for _ in range(4):
                client.poll()
                self.service.poll()
            self.assertIsNone(client.failure)

        drive()
        self.assertEqual(self.rows, [])
        observer.set_views([{'sector_macro': SECTOR, 'indices': [NPC_INDEX]}])
        own = {'version': 3, 'epoch': REGISTRY_EPOCH, 'generation': source.local_generation,
               'index': PILOT_INDEX, 'sequence': 1, 'sector_macro': SECTOR, 'source_time': 10.,
               'capture_time': time.monotonic(), 'x': 5., 'y': 8., 'z': 6., 'real_velocity': None}
        probe = {'epoch': REGISTRY_EPOCH, 'index': NPC_INDEX, 'code': 'MFQ-419', 'sector_macro': SECTOR,
                 'actor': '0x41961', 'sequence': 1, 'attention': 'inzone', 'position': [1., 2., 3.],
                 'game_time': 10., 'capture_time': time.monotonic(),
                 'velocity_m_per_game_second': [2., 0., 1.]}
        drive()
        self.assertEqual(client.stats()['samples_acknowledged'], 1)
        self.assertEqual(self.rows[0]['sample'], probe)
        probe = RADAR_ENDED
        own = dict(own, sequence=2, source_time=11., capture_time=time.monotonic())
        drive()
        self.assertEqual(self.ingress.retired_reason, 'radar_ended')
        self.assertEqual(client.stats()['samples_acknowledged'], 2)
        self.assertEqual(len(self.rows), 1)

    def test_expiry_does_not_retire_ordinary_pilot_motion(self):
        connection, stream = self.open(self.pilot)
        stream.readline()
        self.view(connection, [NPC_INDEX], 1)
        self.sample(connection)
        with patch('authority.company_motion_service.time.monotonic', return_value=self.ingress.deadline):
            normal = {'version': 3, 'epoch': REGISTRY_EPOCH, 'generation': self.pilot['generation'],
                      'index': PILOT_INDEX, 'sequence': 1, 'sector_macro': SECTOR, 'source_time': 10.,
                      'capture_time': 10., 'x': 5., 'y': 8., 'z': 6., 'real_velocity': None}
            self.send(connection, {'kind': 'motion_sample', 'window': self.service.window, 'sample': normal})
        self.assertEqual(self.ingress.retired_reason, 'timeout')
        self.assertEqual(json.loads(stream.readline())['kind'], 'motion_sampled')
        self.assertEqual(self.service.accepted_samples, 1)
        self.sample(connection, sequence=2)
        self.assertEqual(stream.read(1), b'')
        # A late diagnostic frame closes this movement socket, but does not
        # revoke the pilot's separate admitted grant or ordinary rebind.
        self.assertIn(self.pilot['ticket'], self.service._tickets)
        replacement = socket.create_connection(('127.0.0.1', self.service.port), timeout=1)
        self.addCleanup(replacement.close)
        replacement_stream = replacement.makefile('rb')
        self.addCleanup(replacement_stream.close)
        self.send(replacement, {'kind': 'bind_motion', 'ticket': self.pilot['ticket']})
        self.assertEqual(json.loads(replacement_stream.readline())['last_sequence'], 1)
        self.send(replacement, {'kind': 'motion_sample', 'window': self.service.window,
                                'sample': dict(normal, sequence=2, source_time=11., capture_time=11.)})
        self.assertEqual(json.loads(replacement_stream.readline())['sequence'], 2)
