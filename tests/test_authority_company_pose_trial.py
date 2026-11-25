"""Company pose trial regressions using owned loopback UDP and synthetic game replies."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import select
import socket
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools/dev')]
from authority.company_ships import CompanyShip, CompanyShipCatalogue
from authority_company_pose_trial import CompanyPoseTrial, GamePoseChannel, _paused_poses, verify_reloaded

SOURCE_DIGEST = 'e' * 64

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

    def is_application_current(self, record, *, after_inspect):
        self.after_inspect = after_inspect
        return self.current is record


@unittest.skipUnless(sys.platform == 'linux' and Path('/proc/self/net/udp6').is_file(),
                     'requires Linux IPv4/IPv6 ownership tables')
class CompanyPoseTrialTests(unittest.TestCase):
    def setUp(self):
        self.channel, self.peer = self.open_channel()
        self.sample = source_sample()

    def open_channel(self, ships=None):
        channel = GamePoseChannel(EPOCH)
        self.load_catalogue(channel, ships if ships is not None else {1970: 'WOO-831', INDEX: CODE})
        self.addCleanup(channel.close)
        peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(peer.close)
        peer.connect(('127.0.0.1', channel.port))
        peer.settimeout(2)
        peer.send(f'P|{channel.lease}|HELLO'.encode('ascii'))
        channel.bind(process_identity(), lambda: None)
        self.assertEqual(channel.peer, peer.getsockname())
        return channel, peer

    def load_catalogue(self, channel, ships, epoch=EPOCH):
        entries = tuple(CompanyShip(index, f'[0x{index:x}]', SECTOR, None, code) for index, code in ships.items())
        catalogue = CompanyShipCatalogue(epoch, entries)
        checkpoint = SimpleNamespace(sha256=SOURCE_DIGEST)
        with patch('authority_company_pose_trial.read_company_ship_catalogue', return_value=catalogue) as read:
            self.assertIs(channel.read_company_catalogue(checkpoint), catalogue)
        read.assert_called_once_with(checkpoint, include_captains=True)

    def command(self, action, *, actor=ACTOR, peer=None, channel=None, index=INDEX, sector=SECTOR):
        if peer is None:
            peer, channel = self.peer, self.channel
        row = peer.recv(1025).decode('ascii').split('|')
        self.assertEqual(len(row), 12)
        self.assertEqual(row[:3], ['P', channel.lease, action])
        self.assertRegex(row[3], r'^[0-9a-f]{32}$')
        self.assertEqual(row[4:6], [EPOCH, str(index)])
        self.assertRegex(row[6], r'^[0-9a-f]{32}$')
        self.assertEqual(row[7:9], [sector, '0' if action in ('inspect', 'locate') else actor])
        return row

    def reply(self, command, position=TARGET, *, peer=None, actor=ACTOR, **changes):
        action = command[2]
        status = {'inspect': 'INSPECTED', 'locate': 'LOCATED', 'transfer': 'DISPATCHED',
                  'apply': 'DISPATCHED', 'verify': 'OBSERVED'}[action]
        point = (0, 0, 0) if action in ('apply', 'transfer') else position
        values = dict(request_id=command[3], epoch=command[4], index=command[5], generation=command[6],
                      action=action, status=status, actor=actor, sector=command[7], md=point, ui=point,
                      code='-' if action in ('apply', 'transfer') else CODE)
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
        report['source_sha256'] = SOURCE_DIGEST
        trial.checkpoint_published(checkpoint)
        prior = {'success': True, 'source_sha256': SOURCE_DIGEST, 'final_checkpoint_id': checkpoint.checkpoint_id,
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
        service = ApplicationQueue(SimpleNamespace(sample=self.sample, sector_revision=0))
        return CompanyPoseTrial(self.channel, report), report, service

    def test_missing_velocity_has_no_measured_peak_speed(self):
        trial, report, service = self.trial()
        service.current.sample['real_velocity'] = None
        trial.poll(service)
        self.assertIsNone(report['max_source_speed_mps'])
        self.command('inspect')

    def test_base_game_fighters_use_only_the_identifiers_from_their_own_world(self):
        channel, peer = self.open_channel({1073: 'HVC-420', 1074: 'KHI-630'})
        sector = 'cluster_06_sector002_macro'
        for index, code in ((1073, 'HVC-420'), (1074, 'KHI-630')):
            sample = dict(self.sample, index=index, sector_macro=sector)
            channel.begin('inspect', sample)
            command = self.command('inspect', peer=peer, channel=channel, index=index, sector=sector)
            self.reply(command, peer=peer, code=code)
            self.assertEqual(channel.wait()['code'], code)
        with self.assertRaisesRegex(ValueError, 'foreign pose identity'):
            channel.begin('inspect', self.sample)
        self.assert_no_command(peer)

        channel.begin('inspect', sample)
        command = self.command('inspect', peer=peer, channel=channel, index=1074, sector=sector)
        self.reply(command, peer=peer, code='HVC-420')
        with self.assertRaisesRegex(ValueError, 'pose readers disagree'):
            channel.wait()

    def test_only_the_verified_seat_gets_its_saved_captain_seed(self):
        seed = '2211373338772438693'
        crewed = CompanyShip(INDEX, '[0x3752f]', SECTOR, None, CODE, captain_seed=seed)
        other = CompanyShip(1970, '[0x37544]', SECTOR, None, 'WOO-831')
        with patch('authority_company_pose_trial.read_company_ship_catalogue',
                   return_value=CompanyShipCatalogue(EPOCH, (crewed, other))):
            self.channel.read_company_catalogue(SimpleNamespace(sha256=SOURCE_DIGEST))
        self.channel.begin('inspect', self.sample)
        command = self.peer.recv(1025).decode('ascii').split('|')
        self.assertEqual(13, len(command))
        self.assertEqual(seed, command[-1])
        self.reply(command[:-1])
        self.channel.wait()
        self.channel.begin('inspect', dict(self.sample, index=1970))
        self.reply(self.command('inspect', index=1970), code='WOO-831')
        self.channel.wait()
        self.load_catalogue(self.channel, {INDEX: CODE})
        self.assertEqual({}, self.channel.captain_seeds)

    def test_catalogue_refresh_replaces_ships_and_rejects_a_foreign_epoch(self):
        self.load_catalogue(self.channel, {45: None})
        with self.assertRaisesRegex(ValueError, 'foreign pose identity'):
            self.channel.begin('inspect', self.sample)
        sample = dict(self.sample, index=45)
        self.channel.begin('inspect', sample)
        self.reply(self.command('inspect', index=45), code='NEW-1A2')
        self.assertEqual('NEW-1A2', self.channel.wait()['code'])
        self.channel.begin('inspect', sample)
        self.reply(self.command('inspect', index=45), code='NEW-999')
        with self.assertRaisesRegex(ValueError, 'readers disagree'):
            self.channel.wait()
        with self.assertRaisesRegex(ValueError, 'another registry'):
            self.load_catalogue(self.channel, {INDEX: CODE}, epoch='f' * 32)
        self.assertFalse(self.channel.ship_codes)

    def test_source_travel_stays_within_one_grant_sector_visit_and_running_window(self):
        trial, report, _ = self.trial()

        def observe(x, generation, sector_revision):
            sample = dict(self.sample, x=x, generation=generation)
            service = ApplicationQueue(SimpleNamespace(sample=sample, sector_revision=sector_revision))
            trial.poll(service)
            self.reply(self.command('inspect'), (x, sample['y'], sample['z']))
            trial.poll(service)

        observe(10, GENERATION, 0)
        observe(16, GENERATION, 0)
        self.assertEqual(6, report['max_source_travel_metres'])
        new_generation = 'd' * 32
        observe(100, new_generation, 0)
        observe(200, new_generation, 2)  # A visit to another sector between consumed samples.
        trial.start_window()
        observe(300, new_generation, 2)
        self.assertEqual(6, report['max_source_travel_metres'])
        observe(312, new_generation, 2)
        self.assertEqual(12, report['max_source_travel_metres'])

    def test_pause_reports_why_a_previously_verified_ship_has_no_saved_pose(self):
        trial, report, service = self.trial()
        self.move(trial, service)
        self.read_pause(trial, status='PILOT_PRESENT')
        self.assertEqual({}, report['paused'])
        self.assertEqual({str(INDEX): 'PILOT_PRESENT'}, report['unavailable_at_pause'])
        self.assertEqual(0, report['unavailable_before_write'])
        trial.start_window()
        self.assertEqual({}, report['unavailable_at_pause'])

    def test_docked_ship_does_not_block_another_pilot_before_write(self):
        trial, report, service = self.trial()
        trial.poll(service)
        self.reply(self.command('inspect'), status='DOCKING_OR_STORED')
        other = ApplicationQueue(SimpleNamespace(sample=dict(self.sample, index=1970), sector_revision=0))
        trial.poll(other)
        self.reply(self.command('inspect', index=1970), code='WOO-831')
        trial.poll(other)
        self.assertEqual(1, report['unavailable_before_write'])
        self.assertEqual({'1970'}, set(report['last_verified']))
        self.assert_no_command()

    def test_new_docking_or_pilot_before_write_allows_another_ship_to_move(self):
        cases = (
            ('apply', SECTOR, 'DOCKING_OR_STORED'),
            ('transfer', 'cluster_06_sector002_macro', 'DOCKING_OR_STORED'),
            ('apply', SECTOR, 'PILOT_PRESENT'),
            ('transfer', 'cluster_06_sector002_macro', 'PILOT_PRESENT'),
        )
        for write, sector, status in cases:
            with self.subTest(write=write, status=status):
                sample = dict(self.sample, sector_macro=sector)
                service = ApplicationQueue(SimpleNamespace(sample=sample, sector_revision=1))
                report = {}
                trial = CompanyPoseTrial(self.channel, report)
                trial.poll(service)
                read = 'inspect' if write == 'apply' else 'locate'
                self.reply(self.command(read, sector=sector), ORIGIN, sector=SECTOR)
                trial.poll(service)
                self.reply(self.command(write, sector=sector), status=status)

                other_actor = '0x20000000000020'
                other = ApplicationQueue(SimpleNamespace(sample=dict(self.sample, index=1970), sector_revision=0))
                trial.poll(other)
                self.reply(self.command('inspect', index=1970), ORIGIN, actor=other_actor, code='WOO-831')
                trial.poll(other)
                self.reply(self.command('apply', index=1970, actor=other_actor), actor=other_actor)
                trial.poll(other)
                self.reply(self.command('verify', index=1970, actor=other_actor), actor=other_actor, code='WOO-831')
                trial.poll(other)
                self.assertEqual(1, report['unavailable_before_write'])
                self.assertEqual({status: 1}, report['unavailable_reasons'])
                self.assertEqual({'index': INDEX, 'sequence': sample['sequence'], 'stage': write, 'reason': status},
                                 report['last_unavailable_before_write'])
                self.assertEqual(1, report['writes_verified'])
                self.assertEqual({'1970'}, set(report['last_verified']))
                self.assertEqual(SECTOR, self.channel.ship_sectors[INDEX])
                self.assert_no_command()

    def test_unavailable_reply_with_wrong_fields_or_after_dispatch_is_fatal(self):
        variants = (
            ('apply', {'actor': '0'}),
            ('apply', {'sector': 'wrong_sector'}),
            ('transfer', {'generation': 'e' * 32}),
            ('transfer', {'ui': (0, 1, 0)}),
            ('apply', {'code': CODE}),
            ('apply', {'status': 'BRIDGE_LOCAL_REFUSED'}),
            ('apply', {'status': 'PILOT_PRESENT', 'actor': '0'}),
            ('transfer', {'status': 'PILOT_PRESENT', 'sector': 'wrong_sector'}),
            ('verify', {'status': 'PILOT_PRESENT'}),
            ('verify', {}),
        )
        for action, changes in variants:
            with self.subTest(action=action, changes=changes):
                channel, peer = self.open_channel()
                channel.begin(action, self.sample, ACTOR)
                command = self.command(action, peer=peer, channel=channel)
                self.reply(command, peer=peer, **({'status': 'DOCKING_OR_STORED'} | changes))
                with self.assertRaisesRegex(ValueError, 'invalid pre-write ship availability|pending command|X4 refused') as error:
                    channel.poll()
                self.assertIs(type(error.exception), ValueError)
                self.assertIsNotNone(channel.pending)
                self.assert_no_command(peer)

    def test_sector_transfer_uses_both_frames_and_stops_transferring_on_arrival(self):
        target_sector = 'cluster_06_sector002_macro'
        sample = dict(self.sample, sector_macro=target_sector)
        source = SimpleNamespace(sample=sample, sector_revision=0)
        service = ApplicationQueue(source)
        report = {}
        trial = CompanyPoseTrial(self.channel, report)

        trial.poll(service)
        self.assert_no_command()
        source = SimpleNamespace(sample=sample, sector_revision=1)
        service = ApplicationQueue(source)
        trial.poll(service)
        self.reply(self.command('locate', sector=target_sector), position=TARGET, sector=SECTOR)
        trial.poll(service)
        self.reply(self.command('transfer', sector=target_sector))
        trial.poll(service)
        self.reply(self.command('verify', sector=target_sector), status='FRAME_INVALID')
        trial.poll(service)
        self.reply(self.command('verify', sector=target_sector), position=TARGET)
        trial.poll(service)
        self.assertEqual(target_sector, self.channel.ship_sectors[INDEX])
        self.assertEqual(1, report['sector_transfers_verified'])
        self.assertEqual(0, report['max_verified_step_metres'])
        self.assertEqual(SECTOR, report['last_sector_transfer']['source_sector_macro'])
        service = ApplicationQueue(SimpleNamespace(sample=dict(sample, sequence=9), sector_revision=1))
        trial.poll(service)
        self.reply(self.command('inspect', sector=target_sector))
        trial.poll(service)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(trial.after_pause)
            self.reply(self.command('inspect', sector=target_sector))
            future.result(2)
        self.assertTrue(_paused_poses(report, self.channel.ship_codes)[INDEX][2])
        report['paused'][str(INDEX)]['movement']['source_sector_macro'] = target_sector
        with self.assertRaisesRegex(ValueError, 'invalid checkpoint sector transfer'):
            _paused_poses(report, self.channel.ship_codes)
        self.assert_no_command()

    def test_cross_sector_locate_requires_current_grant_and_known_origin(self):
        target_sector = 'cluster_06_sector002_macro'
        for revoked, actual_sector in ((True, SECTOR), (False, 'another_sector_macro')):
            with self.subTest(revoked=revoked, actual_sector=actual_sector):
                sample = dict(self.sample, sector_macro=target_sector)
                service = ApplicationQueue(SimpleNamespace(sample=sample, sector_revision=1))
                trial = CompanyPoseTrial(self.channel, {})
                trial.poll(service)
                self.reply(self.command('locate', sector=target_sector), sector=actual_sector)
                if revoked:
                    service.current = None
                trial.poll(service)
                self.assert_no_command()
                self.assertEqual(SECTOR, self.channel.ship_sectors[INDEX])

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

    def test_only_completed_obsolete_origin_inspection_can_be_discarded(self):
        trial, report, service = self.trial()
        trial.poll(service)
        inspection = self.command('inspect')
        service.current = None
        drained = dict(status='BRIDGE_INSPECT_DRAINED', actor='0', md=(0, 0, 0), ui=(0, 0, 0), code='-')
        self.reply(inspection, **drained)
        trial.poll(service)
        self.assertIsNone(self.channel.pending)
        self.assertEqual(report['drained_obsolete_inspections'], 1)
        self.assertEqual(report['writes_verified'], 0)
        self.assert_no_command()
        current = SimpleNamespace(sample=dict(self.sample, sequence=9), sector_revision=0)
        service.queue.append(current)
        service.current = current
        trial.poll(service)
        next_inspection = self.command('inspect')
        self.assertNotEqual(next_inspection[3], inspection[3])
        self.reply(next_inspection, **drained)
        with self.assertRaisesRegex(ValueError, 'BRIDGE_INSPECT_DRAINED'):
            trial.poll(service)
        self.assert_no_command()

    def test_drain_status_never_applies_to_a_write_or_malformed_receipt(self):
        for action, actor, actual_actor in (('inspect', '0', ACTOR), ('apply', ACTOR, '0'), ('verify', ACTOR, '0')):
            with self.subTest(action=action):
                channel, peer = self.open_channel()
                channel.begin(action, self.sample, actor)
                self.reply(self.command(action, peer=peer, channel=channel), peer=peer, actor=actual_actor,
                           status='BRIDGE_INSPECT_DRAINED', md=(0, 0, 0), ui=(0, 0, 0), code='-')
                with self.assertRaisesRegex(ValueError, 'invalid drained inspection receipt'):
                    channel.poll()
                self.assertIsNotNone(channel.pending)

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

    def test_pose_verification_uses_one_four_second_window_across_reads(self):
        trial, report, service = self.trial()
        with patch('authority_company_pose_trial.time.monotonic', return_value=100.) as clock:
            trial.poll(service)
            self.reply(self.command('inspect'), ORIGIN)
            trial.poll(service)
            self.reply(self.command('apply'))
            trial.poll(service)
            first = self.command('verify')
            clock.return_value = 102.01597
            self.reply(first, ORIGIN)
            trial.poll(service)
            second = self.command('verify')
            self.assertNotEqual(second[3], first[3])
            clock.return_value = 104.
            self.reply(second, TARGET)
            with self.assertRaises(TimeoutError):
                trial.poll(service)
        self.assertEqual(report['writes_verified'], 0)
        self.assert_no_command()

    def test_revoked_grant_after_inspection_does_not_dispatch_apply(self):
        trial, report, service = self.trial()
        trial.poll(service)
        inspection = self.command('inspect')
        service.current = None
        self.reply(inspection, ORIGIN)
        trial.poll(service)
        self.assertTrue(service.after_inspect)
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

        report['source_sha256'] = SOURCE_DIGEST
        trial.checkpoint_published(checkpoint)
        self.assertEqual(checkpoint.checkpoint_id, report['saved_checkpoint_id'])
        self.assertEqual(checkpoint.sha256, report['saved_checkpoint_sha256'])
        prior = {'success': True, 'source_sha256': SOURCE_DIGEST, 'final_checkpoint_id': checkpoint.checkpoint_id,
                 'final_checkpoint_sha256': checkpoint.sha256, 'company_pose_trial': report}

        cold, peer = self.open_channel()
        for key, wrong in (('saved_checkpoint_id', 'other-pose'), ('saved_checkpoint_sha256', 'd' * 64),
                           ('source_sha256', 'd' * 64)):
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
                service = ApplicationQueue(SimpleNamespace(sample=sample, sector_revision=0))
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
        self.channel.ship_sectors[other_index] = other_sector
        other_sample = dict(self.sample, index=other_index, sector_macro=other_sector)
        other = ApplicationQueue(SimpleNamespace(sample=other_sample, sector_revision=0))
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

    def test_unversioned_proof_is_rejected_before_readback(self):
        trial, report, service = self.trial()
        self.move(trial, service)
        self.read_pause(trial)
        prior, checkpoint = self.saved_prior(trial, report)
        prior['engine_operations'] = ['pause', 'save', 'resume', 'pause', 'save']
        del report['movement_proof_version']
        for key in ('generation', 'md_position', 'target', 'movement'):
            del report['paused'][str(INDEX)][key]
        for key in ('generation', 'sector_macro'):
            del report['largest_verified_move'][key]

        with self.assertRaisesRegex(ValueError, 'unsupported checkpoint movement proof'):
            verify_reloaded(self.channel, prior, checkpoint)
        self.assert_no_command()


if __name__ == '__main__':
    unittest.main()
