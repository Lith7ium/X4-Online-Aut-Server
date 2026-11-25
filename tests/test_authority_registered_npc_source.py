"""Offline checkpoint binding, owned native IPC and read-only service ingress."""
import os
from pathlib import Path
import socket
import tempfile
import time
import unittest

from authority.company_motion_service import CompanyMotionService
from authority_registered_npc_source import NpcCheckpointTarget, RegisteredNpcSource


EPOCH = 'a' * 32
SAVED = {'class': 'ship_s', 'id': '[0x101]', 'owner': 'argon', 'code': 'ABC-401'}
SNAPSHOT = {'epoch': EPOCH, 'issued_count': 100, 'source_registry_checked': True}


def target():
    request = NpcCheckpointTarget('40', 'ABC-401')
    request.observe('component', 5, SAVED, 40)
    return request.confirmed(SNAPSHOT)


class NpcCheckpointTargetTests(unittest.TestCase):
    def test_refuses_wrong_or_unverified_saved_target(self):
        request = NpcCheckpointTarget('40', 'ABC-401')
        request.observe('component', 5, SAVED, 39)
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            request.confirmed(SNAPSHOT)
        for change in (dict(owner='player'), dict(code='ABC-402'), dict(id='[0x0]'), dict(**{'class': 'station'})):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'saved identity'):
                request.observe('component', 5, dict(SAVED, **change), 40)
        request.observe('component', 5, SAVED, 40)
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            request.confirmed(dict(SNAPSHOT, source_registry_checked=False))
        self.assertEqual(request.confirmed(SNAPSHOT), target())


class RegisteredNpcSourceTests(unittest.TestCase):
    def setUp(self):
        self.now = time.monotonic()
        self.unreserved = True
        self.service = CompanyMotionService()
        self.addCleanup(self.service.close)
        self.source = RegisteredNpcSource(target(), clock=lambda: self.now)
        self.addCleanup(self.source.close)
        self.source.attach(self.service, lambda: self.unreserved)
        image = Path('/proc/self/exe').stat()
        ticks = int(Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19])
        self.source.bind(dict(pid=os.getpid(), start_ticks=ticks, exe_dev=image.st_dev, exe_ino=image.st_ino))
        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sender.connect(self.source._socket.getsockname())
        self.addCleanup(self.sender.close)

    def send(self, sequence=1, *, sector='sector_a', code='ABC-401', owner='argon', velocity=None):
        rates = ('-', '-', '-') if velocity is None else tuple(str(value) for value in velocity)
        row = ('SAMPLE3', EPOCH, '40', str(sequence), code, owner, sector, str(sequence),
               str(self.now), '45.125', '-2.25', '3000.5', *rates)
        self.sender.send(self.source._prefix + '|'.join(row).encode('ascii'))

    def observation(self):
        return self.service._npc_observation

    def test_owned_sample_has_exact_identity_position_and_velocity_without_application_authority(self):
        self.source.poll()
        self.assertIsNone(self.observation())
        self.service.set_running(True)
        self.send()
        self.source.poll()
        self.now = time.monotonic()
        self.send(2, velocity=(2.75, 0, -11.625))
        self.source.poll()

        observation = self.observation()
        self.assertEqual((observation.binding.epoch, observation.binding.index, observation.binding.ship_code),
                         (EPOCH, 40, 'ABC-401'))
        sample = observation.last_sample
        self.assertEqual([sample[axis] for axis in 'xyz'], [45.125, -2.25, 3000.5])
        self.assertEqual(sample['real_velocity'], {'x': 2.75, 'y': 0, 'z': -11.625})
        self.assertEqual(sample['capture_time'], self.now)
        self.assertEqual(self.source.stats()['recent_publications'][-1], sample)
        self.assertFalse(self.service._grants)
        self.assertFalse(self.service._application_pending)

    def test_bad_live_identity_retires_without_publishing_a_queued_sample(self):
        self.service.set_running(True)
        self.send()
        self.source.poll()
        self.now = time.monotonic()
        self.send(2)
        self.send(3, owner='teladi')
        self.source.poll()
        self.assertIsNone(self.observation())
        self.assertEqual(self.source.stats()['samples'], 1)
        self.assertEqual(self.source.stats()['end_reason'], 'invalid_native_sample')

    def test_native_end_prevents_a_new_generation(self):
        self.service.set_running(True)
        self.send()
        self.source.poll()
        self.sender.send(self.source._prefix + b'END')
        self.source.poll()
        self.assertIsNone(self.observation())
        self.assertEqual(self.source.stats()['end_reason'], 'native_source_ended')
        self.now = time.monotonic()
        self.send(2)
        self.source.poll()
        self.assertIsNone(self.observation())

    def test_lost_source_and_reservation_fail_closed(self):
        self.service.set_running(True)
        self.send()
        self.source.poll()
        self.unreserved = False
        self.source.poll()
        self.assertIsNone(self.observation())
        self.assertEqual(self.source.stats()['end_reason'], 'source_guard_failed')

    def test_missing_samples_expire(self):
        self.service.set_running(True)
        self.send()
        self.source.poll()
        self.now += 3
        self.source.poll()
        self.assertIsNone(self.observation())
        self.assertEqual(self.source.stats()['end_reason'], 'source_expired')

    def test_pause_retires_and_window_changes_do_not_restart(self):
        self.service.set_running(True)
        self.send()
        self.source.poll()
        self.service.set_running(False)
        self.source.poll()
        self.assertEqual(self.source.stats()['end_reason'], 'motion_window_ended')
        self.service.set_running(True)
        self.now = time.monotonic()
        self.send(2)
        self.source.poll()
        self.assertIsNone(self.observation())

    def test_staging_uses_single_registry_ready_update_and_archives_read_only_cue(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            case = out / 'case'
            lab = case / 'extensions/x4_lan_lab'
            (lab / 'ui').mkdir(parents=True)
            (lab / 'md').mkdir()
            bridge = lab / 'ui/authority_world_bridge_probe.lua'
            bridge.write_text('local registry_checkpoint\n local ok,reason=bridge:update();assert(ok,reason)\n')
            manifest = lab / 'ui.xml'
            manifest.write_text('    <file name="ui/authority_world_bridge_probe.lua" />\n')
            files = self.source.install(case)
            self.assertTrue(all(path.is_file() for path in files))
            self.assertIn('registry_checkpoint:ready()', bridge.read_text())
            self.assertIn('actor="0x101"', bridge.read_text())
            descriptor = lab / 'md/authority_job_descriptor_probe.xml'
            self.source.archive_cue(out)
            self.assertFalse(descriptor.exists())
            self.assertTrue((out / descriptor.name).is_file())
