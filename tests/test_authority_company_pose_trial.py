"""Company pose trial regressions using owned loopback UDP and synthetic game replies."""
from collections import deque
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import select
import socket
import sys
import time
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools/dev')]
from authority_company_pose_trial import CompanyPoseTrial, GamePoseChannel, PARKED_SAVE, verify_reloaded

EPOCH = 'a' * 32
GENERATION = 'b' * 32
SECTOR = 'cluster_01_sector001_macro'
INDEX = 7354
CODE = 'WRN-302'
ACTOR = '0x20000000000019'
RELOADED_ACTOR = '0x20000000000120'
ORIGIN = (-400.5, 125.0, 10.0)
TARGET = (-365.25, 129.0, 7.0)


def process_identity():
    proc = Path('/proc/self')
    image = (proc / 'exe').stat()
    fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    return {'pid': os.getpid(), 'start_ticks': int(fields[19]),
            'exe_dev': image.st_dev, 'exe_ino': image.st_ino}


def source_sample():
    return dict(epoch=EPOCH, generation=GENERATION, index=INDEX, sector_macro=SECTOR, sequence=8,
                source_time=11.5, capture_time=20.0, real_velocity={'x': 5, 'y': 0, 'z': 0},
                **dict(zip('xyz', TARGET)))


class ApplicationQueue:
    def __init__(self, record):
        self.queue = deque([record])
        self.current = record

    def take_application_batch(self, limit):
        return [self.queue.popleft() for _ in range(min(limit, len(self.queue)))]

    def is_application_current(self, record):
        return self.current is record


@unittest.skipUnless(sys.platform == 'linux' and Path('/proc/self/net/udp6').is_file(),
                     'requires Linux IPv4/IPv6 ownership tables')
class CompanyPoseTrialTests(unittest.TestCase):
    def setUp(self):
        self.channel, self.peer = self.open_channel()
        self.sample = source_sample()

    def open_channel(self):
        channel = GamePoseChannel(EPOCH)
        self.addCleanup(channel.close)
        peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(peer.close)
        peer.connect(('127.0.0.1', channel.port))
        peer.settimeout(2)
        peer.send(f'P|{channel.lease}|HELLO'.encode('ascii'))
        channel.bind(process_identity(), lambda: None)
        self.assertEqual(channel.peer, peer.getsockname())
        return channel, peer

    def command(self, action, *, actor=ACTOR, peer=None, channel=None, index=INDEX, sector=SECTOR):
        if peer is None:
            peer, channel = self.peer, self.channel
        row = peer.recv(1025).decode('ascii').split('|')
        self.assertEqual(len(row), 12)
        self.assertEqual(row[:3], ['P', channel.lease, action])
        self.assertRegex(row[3], r'^[0-9a-f]{32}$')
        self.assertEqual(row[4:6], [EPOCH, str(index)])
        self.assertRegex(row[6], r'^[0-9a-f]{32}$')
        self.assertEqual(row[7:9], [sector, '0' if action == 'inspect' else actor])
        return row

    def reply(self, command, position=TARGET, *, peer=None, actor=ACTOR, **changes):
        action = command[2]
        status = {'inspect': 'INSPECTED', 'apply': 'DISPATCHED', 'verify': 'OBSERVED'}[action]
        point = (0, 0, 0) if action == 'apply' else position
        values = dict(request_id=command[3], epoch=command[4], index=command[5], generation=command[6],
                      action=action, status=status, actor=actor, sector=command[7], md=point, ui=point,
                      code='-' if action == 'apply' else CODE)
        values.update(changes)
        md, ui, code = (values.pop(key) for key in ('md', 'ui', 'code'))
        data = '|'.join(str(value) for value in ('P', command[1], 'RESULT', *values.values(), *md, *ui, code))
        (peer or self.peer).send(data.encode('ascii'))

    def move(self, trial, service):
        trial.poll(service)
        self.reply(self.command('inspect'), ORIGIN)
        trial.poll(service)
        self.reply(self.command('apply'))
        self.reply(self.wait_for_verify(trial, service))
        trial.poll(service)

    def read_pause(self, trial, **changes):
        with ThreadPoolExecutor(max_workers=1) as pool:
            paused = pool.submit(trial.after_pause)
            self.reply(self.command('inspect'), **changes)
            paused.result(2)
        self.assert_no_command()

    def saved_prior(self, trial, report):
        checkpoint = SimpleNamespace(checkpoint_id='isolated-pose', sha256='c' * 64)
        report['source_sha256'] = PARKED_SAVE
        trial.checkpoint_published(checkpoint)
        prior = {'success': True, 'final_checkpoint_id': checkpoint.checkpoint_id,
                 'final_checkpoint_sha256': checkpoint.sha256, 'company_pose_trial': report}
        return prior, checkpoint

    def cold_read(self, prior, checkpoint, **changes):
        cold, peer = self.open_channel()
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(verify_reloaded, cold, prior, checkpoint)
            inspection = self.command('inspect', peer=peer, channel=cold)
            self.reply(inspection, peer=peer, actor=RELOADED_ACTOR, **changes)
            return result.result(2)

    def trial(self):
        report = {}
        service = ApplicationQueue(SimpleNamespace(sample=self.sample))
        return CompanyPoseTrial(self.channel, report), report, service

    def test_missing_velocity_has_no_measured_peak_speed(self):
        trial, report, service = self.trial()
        service.current.sample['real_velocity'] = None
        trial.poll(service)
        self.assertIsNone(report['max_source_speed_mps'])
        self.command('inspect')

    def assert_no_command(self, peer=None):
        self.assertFalse(select.select([peer or self.peer], [], [], 0)[0])

    def wait_for_verify(self, trial, service):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            trial.poll(service)
            if select.select([self.peer], [], [], .01)[0]:
                return self.command('verify')
        self.fail('no follow-up read-only verify arrived')

    def test_channel_ignores_foreign_sender_and_checks_reply_lineage(self):
        self.channel.begin('inspect', self.sample)
        command = self.command('inspect')
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as other:
            other.connect(self.channel.socket.getsockname())
            self.reply(command, peer=other)
        self.assertIsNone(self.channel.poll())
        self.reply(command, generation='f' * 32)
        with self.assertRaisesRegex(ValueError, 'does not match'):
            self.channel.poll()

    def test_channel_requires_independent_readers_and_stable_ship_code(self):
        for change in ({'code': 'WOO-831'}, {'ui': ORIGIN}, {'md': (float('inf'), 0, 0)}):
            with self.subTest(change=change):
                channel, peer = self.open_channel()
                channel.begin('inspect', self.sample)
                command = self.command('inspect', peer=peer, channel=channel)
                self.reply(command, peer=peer, **change)
                with self.assertRaises(ValueError):
                    channel.wait()

    def test_delayed_readback_retries_verify_without_replaying_apply(self):
        trial, report, service = self.trial()
        trial.poll(service)
        inspection = self.command('inspect')
        self.reply(inspection, ORIGIN)
        trial.poll(service)
        application = self.command('apply')
        self.assertEqual(tuple(map(float, application[-3:])), TARGET)
        self.reply(application)
        verification = self.wait_for_verify(trial, service)
        self.reply(verification, ORIGIN)
        follow_up = self.wait_for_verify(trial, service)
        self.assertNotEqual(follow_up[3], verification[3])
        self.assertEqual(0, report['writes_verified'])
        self.reply(follow_up, TARGET)
        trial.poll(service)
        self.assertEqual(1, report['writes_verified'])
        self.assertEqual(ORIGIN, report['last_verified'][str(INDEX)]['pre_write'])
        self.assertEqual(TARGET, report['last_verified'][str(INDEX)]['ui'])
        self.assert_no_command()

    def test_revoked_grant_after_inspection_does_not_dispatch_apply(self):
        trial, report, service = self.trial()
        trial.poll(service)
        inspection = self.command('inspect')
        service.current = None
        self.reply(inspection, ORIGIN)
        trial.poll(service)
        self.assertEqual(1, report['expired_before_write'])
        self.assertEqual(0, report['writes_verified'])
        self.assertIsNone(self.channel.pending)
        self.assert_no_command()

    def test_paused_and_cold_reads_bind_to_saved_checkpoint_and_stable_ship_code(self):
        trial, report, service = self.trial()
        self.move(trial, service)
        self.assertEqual(1, report['writes_verified'])

        checkpoint = SimpleNamespace(checkpoint_id='isolated-pose', sha256='c' * 64)
        trial.checkpoint_published(checkpoint)
        self.assertNotIn('saved_checkpoint_id', report)
        self.assertNotIn('saved_checkpoint_sha256', report)
        self.read_pause(trial)
        item = report['paused'][str(INDEX)]
        self.assertEqual((CODE, TARGET, TARGET, SECTOR, 8),
                         tuple(item[key] for key in ('code', 'position', 'md_position', 'sector_macro', 'sequence')))
        self.assertEqual(ORIGIN, item['movement']['before'])

        report['source_sha256'] = PARKED_SAVE
        trial.checkpoint_published(checkpoint)
        self.assertEqual(checkpoint.checkpoint_id, report['saved_checkpoint_id'])
        self.assertEqual(checkpoint.sha256, report['saved_checkpoint_sha256'])
        prior = {'success': True, 'final_checkpoint_id': checkpoint.checkpoint_id,
                 'final_checkpoint_sha256': checkpoint.sha256, 'company_pose_trial': report}

        cold, peer = self.open_channel()
        for key, wrong in (('saved_checkpoint_id', 'other-pose'), ('saved_checkpoint_sha256', 'd' * 64)):
            with self.subTest(key=key):
                wrong_proof = dict(prior, company_pose_trial=dict(report, **{key: wrong}))
                with self.assertRaises(ValueError):
                    verify_reloaded(cold, wrong_proof, checkpoint)
                self.assert_no_command(peer)

        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(verify_reloaded, cold, prior, checkpoint)
            inspection = self.command('inspect', peer=peer, channel=cold)
            self.reply(inspection, peer=peer, actor=RELOADED_ACTOR)
            self.assertEqual({str(INDEX): {'code': CODE, 'sector_macro': SECTOR, 'expected': TARGET,
                                           'ui': TARGET, 'md': TARGET, 'error_metres': 0.0,
                                           'movement_verified': True}}, result.result(2))
        self.assert_no_command(peer)
        trial.start_window()
        self.assertFalse(report['paused'])
        self.assertNotIn('saved_checkpoint_id', report)
        self.assertNotIn('saved_checkpoint_sha256', report)
        with self.assertRaises(ValueError):
            verify_reloaded(cold, prior, checkpoint)
        self.assert_no_command(peer)

    def test_small_pre_write_error_does_not_dispatch_or_count_as_movement(self):
        trial, report, service = self.trial()
        trial.poll(service)
        near = (TARGET[0] + 0.5, *TARGET[1:])
        self.reply(self.command('inspect'), near)
        trial.poll(service)
        self.assert_no_command()
        self.assertEqual(0, report['writes_verified'])

    def test_verify_inside_target_tolerance_still_requires_observed_step(self):
        trial, report, service = self.trial()
        trial.poll(service)
        before = (TARGET[0] + 1.5, *TARGET[1:])
        self.reply(self.command('inspect'), before)
        trial.poll(service)
        self.reply(self.command('apply'))
        first = self.wait_for_verify(trial, service)
        self.reply(first, (TARGET[0] + 0.8, *TARGET[1:]))
        second = self.wait_for_verify(trial, service)
        self.assertNotEqual(first[3], second[3])
        self.assertEqual(0, report['writes_verified'])
        self.reply(second, TARGET)
        trial.poll(service)
        self.assertEqual(1, report['writes_verified'])

    def test_previous_window_or_grant_cannot_prove_later_checkpoint(self):
        for rollover in ('window', 'grant'):
            with self.subTest(rollover=rollover):
                trial, report, service = self.trial()
                self.move(trial, service)
                sample = dict(self.sample, sequence=9)
                if rollover == 'window':
                    trial.start_window()
                else:
                    sample['generation'] = 'f' * 32
                service = ApplicationQueue(SimpleNamespace(sample=sample))
                trial.poll(service)
                self.reply(self.command('inspect'))
                trial.poll(service)
                self.read_pause(trial)
                self.assertEqual(1, report['writes_verified'])
                self.assertNotIn('movement', report['paused'][str(INDEX)])
                prior, checkpoint = self.saved_prior(trial, report)
                with self.assertRaisesRegex(ValueError, 'checkpoint movement proof'):
                    verify_reloaded(self.channel, prior, checkpoint)
                self.assert_no_command()

    def test_pause_and_cold_readback_check_md_against_the_target(self):
        trial, report, service = self.trial()
        self.move(trial, service)
        readings = {'ui': (TARGET[0] + .75, *TARGET[1:]),
                    'md': (TARGET[0] + 1.25, *TARGET[1:])}
        with self.assertRaisesRegex(ValueError, 'diverged'):
            self.read_pause(trial, **readings)
        self.read_pause(trial)
        prior, checkpoint = self.saved_prior(trial, report)
        with self.assertRaisesRegex(ValueError, 'cold pose differs'):
            self.cold_read(prior, checkpoint, **readings)

    def test_cold_read_checks_stationary_ship_without_borrowing_other_ship_proof(self):
        trial, report, service = self.trial()
        self.move(trial, service)
        other_index = 1970
        other_sector = 'cluster_14_sector001_macro'
        other_code = 'WOO-831'
        other_sample = dict(self.sample, index=other_index, sector_macro=other_sector)
        other = ApplicationQueue(SimpleNamespace(sample=other_sample))
        trial.poll(other)
        self.reply(self.command('inspect', index=other_index, sector=other_sector), code=other_code)
        trial.poll(other)
        with ThreadPoolExecutor(max_workers=1) as pool:
            paused = pool.submit(trial.after_pause)
            self.reply(self.command('inspect'))
            self.reply(self.command('inspect', index=other_index, sector=other_sector), code=other_code)
            paused.result(2)
        prior, checkpoint = self.saved_prior(trial, report)
        cold, peer = self.open_channel()
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(verify_reloaded, cold, prior, checkpoint)
            self.reply(self.command('inspect', peer=peer, channel=cold), peer=peer, actor=RELOADED_ACTOR)
            inspection = self.command('inspect', peer=peer, channel=cold, index=other_index, sector=other_sector)
            self.reply(inspection, peer=peer, actor=RELOADED_ACTOR, code=other_code)
            verified = result.result(2)
        self.assertTrue(verified[str(INDEX)]['movement_verified'])
        self.assertFalse(verified[str(other_index)]['movement_verified'])

    def test_legacy_format_requires_one_resume_and_plausible_same_ship_movement(self):
        trial, report, service = self.trial()
        self.move(trial, service)
        self.read_pause(trial)
        prior, checkpoint = self.saved_prior(trial, report)
        prior['engine_operations'] = ['pause', 'save', 'checkpoint_published', 'resume', 'pause', 'save',
                                      'checkpoint_published']
        del report['movement_proof_version']
        item = report['paused'][str(INDEX)]
        for key in ('generation', 'md_position', 'target', 'movement'):
            del item[key]
        largest = report['largest_verified_move']
        for key in ('generation', 'sector_macro'):
            del largest[key]
        largest['sequence'] = 5
        self.assertEqual(TARGET, self.cold_read(prior, checkpoint)[str(INDEX)]['expected'])
        for bad_field, wrong in (('sequence', 9), ('code', 'WOO-831'), ('before', TARGET),
                                 ('md', ORIGIN), ('ui', ORIGIN), ('target', ORIGIN)):
            with self.subTest(field=bad_field):
                broken = deepcopy(prior)
                broken['company_pose_trial']['largest_verified_move'][bad_field] = wrong
                with self.assertRaises(ValueError):
                    verify_reloaded(self.channel, broken, checkpoint)
                self.assert_no_command()
        for operations in ([], ['resume', 'pause', 'resume', 'pause']):
            with self.subTest(operations=operations):
                with self.assertRaisesRegex(ValueError, 'one resume'):
                    verify_reloaded(self.channel, dict(prior, engine_operations=operations), checkpoint)
                self.assert_no_command()


if __name__ == '__main__':
    unittest.main()
