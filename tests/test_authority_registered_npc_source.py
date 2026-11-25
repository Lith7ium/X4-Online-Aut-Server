"""Offline checkpoint binding, owned native IPC and read-only service ingress."""
import os
from dataclasses import replace
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from authority.checkpoints import Checkpoint
from authority.company_motion_service import CompanyMotionService
from authority.company_npc_measurement import NPC_CODE, NPC_INDEX, REGISTRY_EPOCH
from authority.company_ships import CompanyShip
from authority_recovered_registry_snapshot import RegistrySnapshotState
from authority_registered_npc_source import NpcCheckpointTarget, RegisteredNpcSource


EPOCH = 'a' * 32
SAVED = {'class': 'ship_s', 'id': '[0x101]', 'owner': 'argon', 'code': 'ABC-401'}
SNAPSHOT = {'epoch': EPOCH, 'issued_count': 100, 'source_registry_checked': True, 'format': 3,
            'run_id': 'b' * 32, 'availability_bits': '1' * 100, 'operational_bits': '1' * 100,
            **{key: 'f' * 64 for key in ('availability_sha256', 'operational_sha256', 'mapping_sha256')}}
ROOT = Path(__file__).resolve().parents[1]


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
                mismatched = NpcCheckpointTarget('40', 'ABC-401')
                mismatched.observe('component', 5, dict(SAVED, **change), 40)
                mismatched.confirmed(SNAPSHOT)
        request.observe('component', 5, SAVED, 40)
        for changes in (dict(source_registry_checked=False), dict(operational_bits='1' * 39 + '0' + '1' * 60),
                        dict(availability_bits='1' * 39 + '0' + '1' * 60)):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'unavailable'):
                request.confirmed(dict(SNAPSHOT, **changes))
        self.assertEqual(request.confirmed(SNAPSHOT), target())

    @unittest.skipUnless(shutil.which('luajit'), 'requires LuaJIT')
    def test_native_bootstrap_binds_runtime_handle_and_refuses_wrong_code_or_owner(self):
        subprocess.run(['luajit', 'tests/authority_registered_npc_source.lua'], cwd=ROOT, check=True, timeout=5)


class RegisteredNpcMeasurementTests(unittest.TestCase):
    def test_opt_in_installs_only_for_confirmed_trial_target(self):
        for mismatch in ({'epoch': EPOCH}, {'index': 40}, {'code': 'ABC-401'}):
            selection = {'epoch': REGISTRY_EPOCH, 'index': NPC_INDEX, 'code': NPC_CODE, **mismatch}
            with self.subTest(mismatch=mismatch), self.assertRaisesRegex(ValueError, 'confirmed MFQ-419'):
                RegisteredNpcSource(selection, trial_measurement=True)

        selection = dict(target(), epoch=REGISTRY_EPOCH, index=NPC_INDEX, code=NPC_CODE)
        service = CompanyMotionService()
        self.addCleanup(service.close)
        default_source = RegisteredNpcSource(selection)
        self.addCleanup(default_source.close)
        source = RegisteredNpcSource(selection, trial_measurement=True)
        self.addCleanup(source.close)

        def guard():
            return False

        default_source.attach(service, guard)
        self.assertIsNone(service._npc_measurement)
        self.assertNotIn('trial_measurement', default_source.stats())
        rows = []
        source.attach(service, guard, on_measurement=rows.append)
        ingress = service._npc_measurement
        self.assertIs(ingress.guard, guard)
        self.assertEqual(ingress.on_sample, rows.append)
        self.assertEqual(source.stats()['trial_measurement'], {'accepted': 0, 'end_reason': None})
        ingress.deadline = 2
        ingress.check(1)
        source.close()
        self.assertEqual(source.stats()['trial_measurement'],
                         {'accepted': 0, 'end_reason': 'provider_binding_lost'})
        self.assertEqual(rows, [])


class RegisteredNpcSourceTests(unittest.TestCase):
    def setUp(self):
        self.now = time.monotonic()
        self.unreserved = True
        self.service = CompanyMotionService()
        self.addCleanup(self.service.close)
        self.source = RegisteredNpcSource(target(), clock=lambda: self.now)
        self.addCleanup(self.source.close)
        self.source.attach(self.service, lambda: self.unreserved)
        if sys.platform.startswith('linux'):
            image = Path('/proc/self/exe').stat()
            ticks = int(Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19])
            identity = dict(pid=os.getpid(), start_ticks=ticks, exe_dev=image.st_dev, exe_ino=image.st_ino)
        else:
            # No /proc (Windows): the UDP ownership lookup is Linux-only, so accept exactly
            # this test's connected sender, bound identity and source socket instead.
            image = Path(sys.executable).stat()
            identity = dict(pid=os.getpid(), start_ticks=0, exe_dev=image.st_dev, exe_ino=image.st_ino)
            owner = patch('authority_registered_npc_source.socket_owner',
                          side_effect=lambda peer, local, process: (
                              process == identity and peer == self.sender.getsockname()
                              and local == self.source._socket.getsockname()))
            owner.start()
            self.addCleanup(owner.stop)
        self.source.bind(identity)
        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sender.connect(self.source._socket.getsockname())
        self.addCleanup(self.sender.close)

    def send(self, sequence=1, *, sector='sector_a', code='ABC-401', owner='argon', actor='0x301', velocity=None):
        rates = ('-', '-', '-') if velocity is None else tuple(str(value) for value in velocity)
        row = ('SAMPLE3', EPOCH, '40', str(sequence), code, owner, actor, sector, str(sequence),
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
        # The next capture must be later. On Windows 3.11 time.monotonic ticks every 15.625 ms.
        first = self.now
        while (now := time.monotonic()) <= first:
            time.sleep(0.001)
        self.now = now
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
        self.assertEqual(self.source.stats()['saved_component_ref'], '0x101')
        self.assertEqual(self.source.stats()['runtime_component_ref'], '0x301')
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

    def test_runtime_remap_retires_even_when_epoch_index_code_and_owner_match(self):
        self.service.set_running(True)
        self.send()
        self.source.poll()
        self.now = time.monotonic()
        self.send(2, actor='0x401')
        self.source.poll()
        self.assertIsNone(self.observation())
        self.assertEqual(self.source.stats()['runtime_component_ref'], '0x301')

    def test_confirmed_join_save_rebinds_and_checks_the_current_offer(self):
        cold = Checkpoint('cold', Path('/cold'), 'a' * 64, 100, SNAPSHOT['run_id'])
        join = Checkpoint('join', Path('/join'), 'b' * 64, 101, 'c' * 32)
        state = RegistrySnapshotState(cold, SNAPSHOT)
        state.initial_inspect_verified = True
        candidate = self.source.join_candidate()
        snapshot = dict(SNAPSHOT, run_id=join.marker)

        def scan(checkpoint, *, observe_universe):
            self.assertEqual(checkpoint, join)
            observe_universe('component', 5, dict(SAVED, id='[0x201]'), 40)
            return snapshot

        with patch('authority_recovered_registry_snapshot.verify_join_snapshot', side_effect=scan), \
                patch('authority_recovered_registry_snapshot.bridge_gate.verify_bridge_observation'):
            state.verify_save(join, Path('/join.log'), observe_registry=candidate.observe)
        self.source.confirm_join_checkpoint(join, state.latest_evidence, candidate)
        self.assertTrue(self.source.reserved_or_unverified(cold, ()))
        self.assertTrue(self.source.reserved_or_unverified(replace(join, sha256='d' * 64), ()))
        self.assertFalse(self.source.reserved_or_unverified(join, ()))
        alias = CompanyShip(96, '[0x201]', 'sector_a', None, 'ALT-096')
        self.assertTrue(self.source.reserved_or_unverified(join, (alias,)))
        self.assertEqual(self.source.stats()['join_component_ref'], '[0x201]')
        self.assertEqual(self.source.stats()['join_checkpoint_id'], 'join')
        different_owner = self.source.join_candidate()
        different_owner.observe('component', 5, dict(SAVED, owner='teladi'), 40)
        self.source.confirm_join_checkpoint(join, snapshot, different_owner)
        self.assertTrue(self.source.reserved_or_unverified(join, ()))

    def test_native_end_prevents_a_new_generation(self):
        self.service.set_running(True)
        self.send()
        self.source.poll()
        self.sender.send(self.source._prefix + b'END|code_mismatch')
        self.source.poll()
        self.assertIsNone(self.observation())
        self.assertEqual(self.source.stats()['end_reason'], 'native_code_mismatch')
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

    def test_late_poll_drains_continuous_captures_and_publishes_only_fresh_samples(self):
        self.service.set_running(True)
        with patch('authority.company_motion_service.time.monotonic', side_effect=lambda: self.now):
            self.send()
            self.source.poll()
            self.now += .1
            self.send(2)
            self.now += 1.4
            self.send(3, velocity=(7.0, 0.0, 0.0))
            self.now += .8
            self.source.poll()
        stats = self.source.stats()
        self.assertIsNone(stats['end_reason'])
        self.assertEqual(stats['stale_samples_discarded'], 1)
        self.assertGreater(stats['longest_running_poll_gap'], 2)
        self.assertEqual([row['sequence'] for row in stats['recent_publications']], [1, 3])
        self.assertEqual(self.observation().last_sample['real_velocity']['x'], 7.0)
        self.assertFalse(self.service._application_pending)

    def test_fresh_packet_after_silent_source_deadline_cannot_revive_it(self):
        self.service.set_running(True)
        self.send()
        self.source.poll()
        self.now += 2.5
        self.send(2)
        self.source.poll()
        self.assertEqual(self.source.stats()['end_reason'], 'source_expired')
        self.assertIsNone(self.observation())

    def test_missing_samples_expire(self):
        self.service.set_running(True)
        self.send()
        self.source.poll()
        self.now += 3
        self.source.poll()
        self.assertIsNone(self.observation())
        self.assertEqual(self.source.stats()['end_reason'], 'source_expired')

    def test_initial_runtime_resolution_has_no_two_second_python_deadline(self):
        self.service.set_running(True)
        self.now -= 3
        self.source.poll()
        self.now = time.monotonic()
        self.source.poll()
        self.assertIsNone(self.source.stats()['end_reason'])
        self.send()
        self.source.poll()
        self.assertIsNotNone(self.observation())

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

    @unittest.skipUnless(hasattr(os, 'geteuid'), 'staging checks POSIX file ownership (Linux provider)')
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
            self.assertIn('registry_checkpoint:admission_ready()', bridge.read_text())
            self.assertIn('registry_checkpoint:state().current_run_id', bridge.read_text())
            self.assertNotIn('0x101', bridge.read_text())
            descriptor = lab / 'md/authority_job_descriptor_probe.xml'
            self.source.archive_cue(out)
            self.assertFalse(descriptor.exists())
            self.assertTrue((out / descriptor.name).is_file())
