"""Two synthetic sectors over the existing latest-motion TCP/UDP owners.

The selector is an explicit test input, not X4 visibility or map discovery.
No X4 process, native object binding, Internet, or Windows client is involved.
"""
import json
import socket
import threading
import time
import unittest

from tests import test_authority_experimental_tcp_motion as fixture
from tests import test_authority_experimental_tcp_participants as participant_fixture
from tests.test_authority_tcp_client_motion_reentry import MotionReentryController
from authority.experimental_source_motion_ipc import ExperimentalSourceMotionIPC
from authority.experimental_tcp_job_delivery import ExperimentalTCPJobDelivery
from authority.experimental_tcp_motion import ExperimentalTCPMotion, motion_digest
from authority.experimental_tcp_participant_client import ExperimentalTCPParticipantClient

TEST_TIMEOUT = 10
PEER_TIMEOUT = 30
SOURCE_TIMEOUT = 45
POLL_INTERVAL = .005
MOTION_INTERVAL = 1 / 240
EPOCH = 'b' * 32
GENERATION = 'c' * 32
SECTOR_A_TRACK = 10055
SECTOR_B_TRACK = 10056


class SourceLatestTwoSectors(unittest.TestCase):
    allow_sector_reentry = False
    epoch = EPOCH
    sector_a_track = SECTOR_A_TRACK
    sector_b_track = SECTOR_B_TRACK
    saved_baseline = 20000
    origin_sector = 'sector_b'
    other_sector = 'sector_a'

    def setUp(self):
        # Reuse the fixture methods on this running TestCase so their public
        # addCleanup calls are reported by the normal unittest result.
        participant_fixture.ParticipantTCPTest.setUp(self)
        self.stop = threading.Event()
        self.threads = []
        self.errors = []
        self.received = []
        self.expected_disconnect = set()
        self.release_slow_receipt = threading.Event()
        first = participant_fixture.ParticipantTCPTest.connect(self)
        second = participant_fixture.ParticipantTCPTest.connect(self)
        self.addCleanup(fixture.MotionTCP.finish, self)
        self.addCleanup(self.release_slow_receipt.set)
        participant_fixture.ParticipantTCPTest.offer_and_claim(self, first)
        participant_fixture.ParticipantTCPTest.offer_and_claim(self, second)
        fixture.MotionTCP.client(self, first, 1)
        self.start_slow_participant(second)
        self.host.tick(.1)
        self.control.enable_latest_motion(enabled=True, timeout=PEER_TIMEOUT)
        self.delivery = ExperimentalTCPJobDelivery(self.host, enabled=True, timeout=PEER_TIMEOUT)
        self.motion = ExperimentalTCPMotion(self.delivery, enabled=True, min_interval=MOTION_INTERVAL,
                                            allow_sector_reentry=self.allow_sector_reentry)

        local = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        remote = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(local.close)
        self.addCleanup(remote.close)
        local.bind(('127.0.0.1', 0))
        remote.bind(('127.0.0.1', 0))
        local.connect(remote.getsockname())
        remote.connect(local.getsockname())
        remote.settimeout(TEST_TIMEOUT)
        self.remote = remote
        members = self.delivery.membership()
        self.recipients = {self.other_sector: members[0], self.origin_sector: members[1]}

        def select_recipients(sample, _members):
            member = self.recipients.get(sample['sector_macro'])
            return [dict(member)] if member is not None else []

        self.bridge = ExperimentalSourceMotionIPC(
            local, self.motion, enabled=True, lease='1' * 32, attempt='2' * 32,
            epoch=self.epoch, generation=GENERATION, guard=lambda: True,
            validate_peer=lambda peer: peer == remote.getsockname(), timeout=SOURCE_TIMEOUT,
            select_recipients=select_recipients, latest=True,
            saved_baseline=self.saved_baseline,
            saved_guard=lambda epoch, count: epoch == self.epoch and count == self.saved_baseline,
            saved_lifetime_guard=lambda epoch, count: epoch == self.epoch and count == self.saved_baseline,
        )
        self.bridge.bind_confirmed(self.sector_a_track)
        self.bridge.bind_confirmed(self.sector_b_track)

    def start_slow_participant(self, connection):
        """Hold the actual participant callback until the test releases it."""
        def run():
            try:
                connection.settimeout(PEER_TIMEOUT)
                frame = b''
                while not frame.endswith(b'\n'):
                    part = connection.recv(1)
                    if not part:
                        raise RuntimeError('missing admission')
                    frame += part
                if json.loads(frame)['kind'] != 'accepted':
                    raise RuntimeError('unexpected admission')

                def accept(sample, despawn):
                    self.received.append((2, dict(sample), despawn))
                    return self.release_slow_receipt.wait(PEER_TIMEOUT)

                participant = ExperimentalTCPParticipantClient(
                    connection, fixture.Controller(), enabled=True, epoch='a' * 32,
                    handle=2, timeout=PEER_TIMEOUT, apply_motion=accept, apply_job=lambda _: True,
                )
                while not self.stop.is_set():
                    participant.poll(.05)
                participant.close()
            except BaseException as error:
                if not self.stop.is_set() and 2 not in self.expected_disconnect:
                    self.errors.append(error)

        thread = threading.Thread(target=run)
        self.threads.append(thread)
        thread.start()

    def wait_for(self, predicate, timeout=TEST_TIMEOUT):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.host.tick(POLL_INTERVAL)
        self.assertTrue(predicate())

    def _begin_pull(self):
        # The bridge rate limit uses time.monotonic(), which has ~15.6 ms ticks on Windows.
        deadline = time.monotonic() + 1.0
        time.sleep(MOTION_INTERVAL + .001)
        while not self.bridge.begin_latest():
            if time.monotonic() >= deadline:
                self.fail('begin_latest remained rate-limited')
            time.sleep(MOTION_INTERVAL + .001)

    def sample(self, index, sequence, sector, velocity):
        self._begin_pull()
        request = self.remote.recv(769).decode('ascii').split('|')
        self.assertEqual(request[:6], ['P', '1' * 32, '2' * 32, 'PULL', EPOCH, GENERATION])
        self.assertEqual(len(request), 7)
        self.assertTrue(request[6].isdigit() and int(request[6]) > 0)
        response = [
            'S', '1' * 32, '2' * 32, 'SAMPLE', EPOCH, GENERATION,
            request[-1], str(index), str(sequence), sector, str(sequence),
            '1', '2', '3', *velocity,
        ]
        self.remote.send('|'.join(response).encode('ascii'))
        complete, sample = self.bridge.poll_latest()
        self.assertTrue(complete)
        return sample

    def test_sector_selection_slow_peer_and_replaced_incarnation(self):
        original_second = dict(self.recipients['sector_b'])
        slow = self.sample(SECTOR_B_TRACK, 1, 'sector_b', ['-', '-', '-'])
        self.assertIsNone(slow['velocity'])
        self.wait_for(lambda: any(handle == 2 for handle, _, _ in self.received))

        fast = self.sample(SECTOR_A_TRACK, 1, 'sector_a', ['10', '0', '0'])
        self.assertEqual(fast['velocity'], {'x': 10.0, 'y': 0.0, 'z': 0.0})
        fast_track = self.motion._tracks[(EPOCH, GENERATION, SECTOR_A_TRACK)]
        fast_member = self.recipients['sector_a']
        fast_identity = (fast_member['id'], fast_member['incarnation'])
        expected_fast_receipt = (fast['sequence'], motion_digest(fast))
        self.wait_for(lambda: fast_track['peer_samples'].get(fast_identity) == expected_fast_receipt)
        slow_track = self.motion._tracks[(EPOCH, GENERATION, SECTOR_B_TRACK)]
        slow_identity = (original_second['id'], original_second['incarnation'])
        self.assertNotIn(slow_identity, slow_track['peer_samples'])
        self.release_slow_receipt.set()
        self.wait_for(self.control._motion_idle)
        self.assertEqual([(handle, sample['sector_macro']) for handle, sample, _ in self.received],
                         [(2, 'sector_b'), (1, 'sector_a')])

        self.expected_disconnect.add(2)
        old_socket = next(conn for conn, peer in self.control._peers.items() if peer['handle'] == 2)
        self.control._drop(old_socket)
        replacement = participant_fixture.ParticipantTCPTest.connect(self)
        # Mark teardown before closing this socket; thread joining follows later.
        self.addCleanup(self.stop.set)
        self.host.tick(.1)
        participant_fixture.ParticipantTCPTest.offer_and_claim(self, replacement)
        fixture.MotionTCP.client(self, replacement, 3)
        self.host.tick(.1)
        # A subsequent host tick releases the world lifecycle barrier.
        self.host.tick(0)
        self.recipients['sector_b'] = self.delivery.membership()[1]
        self.assertNotEqual(self.recipients['sector_b']['incarnation'], original_second['incarnation'])

        self.sample(SECTOR_B_TRACK, 2, 'sector_b', ['-', '-', '-'])
        self.wait_for(lambda: any(handle == 3 for handle, _, _ in self.received))
        self.wait_for(self.control._motion_idle)
        self.assertNotIn(slow_identity, slow_track['peer_samples'])

        # Even with the new member ID, its predecessor's incarnation is stale.
        self.recipients['sector_b'] = {
            'id': self.recipients['sector_b']['id'],
            'incarnation': original_second['incarnation'],
        }
        with self.assertRaises(ValueError):
            self.sample(SECTOR_B_TRACK, 3, 'sector_b', ['-', '-', '-'])
        self.assertEqual([sample['sequence'] for handle, sample, _ in self.received if handle == 3], [2])


class SourceLatestReentry(unittest.TestCase):
    allow_sector_reentry = True
    epoch = EPOCH
    sector_a_track = SECTOR_A_TRACK
    sector_b_track = SECTOR_B_TRACK
    saved_baseline = 20000
    origin_sector = 'sector_b'
    other_sector = 'sector_a'
    setUp = SourceLatestTwoSectors.setUp
    wait_for = SourceLatestTwoSectors.wait_for
    _begin_pull = SourceLatestTwoSectors._begin_pull
    sample = SourceLatestTwoSectors.sample

    def start_slow_participant(self, connection):
        # Synthetic peer for the actual TCP wire. A separate client test owns
        # the engine-binding callbacks and exactly-once write behavior.
        self.frames = []
        self.slow_connection = connection
        self.prepare_received = threading.Event()
        self.align_received = threading.Event()
        self.release_align = threading.Event()
        self.addCleanup(self.release_align.set)

        def stop_reader():
            self.stop.set()
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

        self.addCleanup(stop_reader)

        def run():
            try:
                connection.settimeout(0.05)
                buffer = b''

                def next_line():
                    nonlocal buffer
                    while b'\n' not in buffer:
                        if self.stop.is_set():
                            return b''
                        try:
                            chunk = connection.recv(4096)
                        except TimeoutError:
                            continue
                        if not chunk:
                            return b''
                        buffer += chunk
                    line, buffer = buffer.split(b'\n', 1)
                    return line + b'\n'

                admitted = next_line()
                if not admitted or json.loads(admitted)['kind'] != 'accepted':
                    raise RuntimeError('unexpected admission')
                while not self.stop.is_set():
                    line = next_line()
                    if not line:
                        break
                    command = json.loads(line)
                    kind = command['kind']
                    if kind == 'participant_command':
                        receipt = dict(command, kind='participant_receipt', status='confirmed')
                        connection.sendall(json.dumps(receipt).encode() + b'\n')
                        continue
                    self.frames.append(command)
                    receipt = {k: v for k, v in command.items() if k not in ('kind', 'sample')}
                    if kind == 'motion_snapshot':
                        status = 'accepted'
                        receipt_kind = 'motion_receipt'
                    elif kind == 'motion_reentry_prepare':
                        self.prepare_received.set()
                        if not self.release_slow_receipt.wait(PEER_TIMEOUT):
                            raise TimeoutError('test did not release prepare')
                        status, receipt_kind = 'prepared', 'motion_reentry_prepared_receipt'
                    elif kind == 'motion_reentry_align':
                        self.align_received.set()
                        if not self.release_align.wait(PEER_TIMEOUT):
                            raise TimeoutError('test did not release align')
                        status, receipt_kind = 'aligned', 'motion_reentry_aligned_receipt'
                    elif kind == 'motion_reentry_cancel':
                        status, receipt_kind = 'canceled', 'motion_reentry_canceled_receipt'
                    else:
                        raise ValueError(kind)
                    if 'sample' in command:
                        receipt['sample_sha256'] = motion_digest(command['sample'])
                    receipt.update(kind=receipt_kind, status=status)
                    connection.sendall(json.dumps(receipt).encode() + b'\n')
            except BaseException as error:
                if not self.stop.is_set():
                    self.errors.append(error)

        thread = threading.Thread(target=run)
        self.threads.append(thread)
        thread.start()

    def begin_v3(self):
        self._begin_pull()
        return self.remote.recv(769).decode('ascii').split('|')[-1]

    def finish_v3(self, request, sequence, sector, rate):
        self.remote.send('|'.join([
            'S', '1' * 32, '2' * 32, 'SAMPLE3', EPOCH, GENERATION,
            request, str(SECTOR_B_TRACK), str(sequence), sector, '14', str(sequence + 40),
            str(sequence * 25), '2', '3', *rate,
        ]).encode('ascii'))
        complete, value = self.bridge.poll_latest()
        self.assertTrue(complete)
        return value

    def request_v3(self, sequence, sector, rate=('-', '-', '-')):
        return self.finish_v3(self.begin_v3(), sequence, sector, rate)

    def begin_return(self, *, other_interested=False):
        old = self.request_v3(1, 'sector_b')
        peer = self.recipients['sector_b']
        self.identity = (peer['id'], peer['incarnation'])
        self.track = self.motion._tracks[(EPOCH, GENERATION, SECTOR_B_TRACK)]
        self.wait_for(lambda: self.track['peer_samples'].get(self.identity) == (1, motion_digest(old)))
        # The real source selector excludes the returning peer, regardless of
        # whether another participant was watching the destination.
        if other_interested:
            self.recipients['sector_c'] = self.recipients['sector_a']
        absent_sample = self.request_v3(2, 'sector_c')
        self.assertEqual(absent_sample is not None, other_interested)
        self.recipients['sector_c'] = peer
        self.request_v3(3, 'sector_c', ('12', '0', '0'))
        self.wait_for(self.prepare_received.is_set)
        self.assertEqual(self.track['peer_frames'][self.identity], 'sector_b')
        prepared = self.frames[-1]
        self.assertEqual(prepared['kind'], 'motion_reentry_prepare')
        self.assertEqual(prepared['previous_sector'], 'sector_b')
        self.assertEqual(prepared['sample']['version'], 3)
        self.assertIsNone(prepared['sample']['real_velocity'])
        return prepared

    def test_align_uses_confirmed_later_pull_while_other_peer_moves(self):
        prepared = self.begin_return(other_interested=True)
        self.sample(SECTOR_A_TRACK, 1, 'sector_a', ['-', '-', '-'])
        self.wait_for(lambda: any(handle == 1 and sample['index'] == SECTOR_A_TRACK
                                  for handle, sample, _ in self.received))
        earlier_request = self.begin_v3()
        self.release_slow_receipt.set()
        self.wait_for(lambda: self.track['reentries'][self.identity]['phase'] == 'prepared')
        self.finish_v3(earlier_request, 4, 'sector_c', ('17', '0', '0'))
        for _ in range(5):
            self.host.tick(POLL_INTERVAL)
        self.assertFalse(self.align_received.is_set())

        fresh = self.request_v3(5, 'sector_c', ('32', '0', '0'))
        self.wait_for(self.align_received.is_set)
        alignment = self.frames[-1]
        self.assertEqual(alignment['sample'], fresh)
        self.assertEqual(alignment['prepared_sequence'], prepared['request_id'])
        self.assertEqual(self.track['peer_frames'][self.identity], 'sector_b')
        self.assertEqual(self.track['peer_samples'][self.identity][0], 1)
        self.release_align.set()
        self.wait_for(lambda: self.track['peer_samples'].get(self.identity) == (5, motion_digest(fresh)))
        self.request_v3(6, 'sector_c', ('33', '0', '0'))
        self.wait_for(self.control._motion_idle)
        self.assertEqual(self.frames[-1]['kind'], 'motion_snapshot')
        self.assertEqual(self.track['peer_frames'][self.identity], 'sector_c')

    def test_interest_loss_during_prepare_cancels_without_disconnect_or_write(self):
        prepared = self.begin_return()
        del self.recipients['sector_c']
        self.assertIsNone(self.request_v3(4, 'sector_c'))
        self.release_slow_receipt.set()
        self.wait_for(self.control._motion_idle)
        self.assertFalse(self.align_received.is_set())
        canceled = self.frames[-1]
        self.assertEqual(canceled['kind'], 'motion_reentry_cancel')
        self.assertEqual(canceled['request_id'], prepared['request_id'])
        self.assertEqual(canceled['prepared_sequence'], prepared['request_id'])
        self.assertEqual(canceled['sample_sha256'], motion_digest(prepared['sample']))
        self.assertEqual(self.delivery.membership()[1], self.recipients['sector_b'])
        self.assertEqual(self.track['peer_frames'][self.identity], 'sector_b')
        self.assertEqual(self.track['peer_samples'][self.identity][0], 1)

    def test_changed_frame_without_positive_absence_is_rejected(self):
        old = self.request_v3(1, 'sector_b')
        peer = self.recipients['sector_b']
        identity = (peer['id'], peer['incarnation'])
        track = self.motion._tracks[(EPOCH, GENERATION, SECTOR_B_TRACK)]
        self.wait_for(lambda: track['peer_samples'].get(identity) == (1, motion_digest(old)))
        self.recipients['sector_c'] = peer
        with self.assertRaisesRegex(ValueError, 'recipient motion frame changed without resync'):
            self.request_v3(2, 'sector_c')
        self.assertFalse(self.prepare_received.is_set())
        self.assertEqual(track['peer_frames'][identity], 'sector_b')

    def test_lifecycle_barrier_drains_cancel_before_release(self):
        self.begin_return()
        self.release_slow_receipt.set()
        self.wait_for(lambda: self.track['reentries'][self.identity]['phase'] == 'prepared')
        self.assertFalse(self.control.motion_barrier())
        self.assertFalse(self.bridge.begin_latest())
        self.wait_for(self.control._motion_idle)
        self.assertEqual(self.frames[-1]['kind'], 'motion_reentry_cancel')
        self.assertEqual(self.track['peer_frames'][self.identity], 'sector_b')
        self.control.release_motion_barrier()
        self.assertEqual(len(self.delivery.membership()), 2)

    def test_unsolicited_duplicate_prepare_receipt_drops_only_that_peer(self):
        self.expected_disconnect.add(2)
        prepared = self.begin_return()
        self.release_slow_receipt.set()
        self.wait_for(lambda: self.track['reentries'][self.identity]['phase'] == 'prepared')
        duplicate = {k: v for k, v in prepared.items() if k not in ('kind', 'sample')}
        duplicate.update(kind='motion_reentry_prepared_receipt', status='prepared',
                         sample_sha256=motion_digest(prepared['sample']))
        self.slow_connection.sendall(json.dumps(duplicate).encode() + b'\n')
        self.wait_for(lambda: len(self.delivery.membership()) == 1)
        self.assertEqual(self.delivery.membership()[0]['id'], self.recipients['sector_a']['id'])
        self.assertNotIn(self.identity, self.track['peer_frames'])
        self.sample(SECTOR_A_TRACK, 1, 'sector_a', ['10', '0', '0'])
        self.wait_for(lambda: any(handle == 1 and sample['index'] == SECTOR_A_TRACK
                                  for handle, sample, _ in self.received))


class SourceLatestReentryLoopback(unittest.TestCase):
    allow_sector_reentry = True
    epoch = EPOCH
    sector_a_track = SECTOR_A_TRACK
    sector_b_track = SECTOR_B_TRACK
    saved_baseline = 20000
    origin_sector = 'sector_b'
    other_sector = 'sector_a'
    setUp = SourceLatestTwoSectors.setUp
    wait_for = SourceLatestTwoSectors.wait_for
    _begin_pull = SourceLatestTwoSectors._begin_pull
    begin_v3 = SourceLatestReentry.begin_v3
    finish_v3 = SourceLatestReentry.finish_v3
    request_v3 = SourceLatestReentry.request_v3

    def start_slow_participant(self, connection):
        # Only the local game callback is synthetic. Both transport endpoints
        # validate and generate their own wire frames and receipts.
        self.game_callback = MotionReentryController()
        fixture.MotionTCP.client(self, connection, 2, motion_reentry_controller=self.game_callback)

    def test_cancel_then_fresh_alignment_with_real_participant(self):
        member = self.recipients['sector_b']
        identity = (member['id'], member['incarnation'])
        track = self.motion._tracks[(EPOCH, GENERATION, SECTOR_B_TRACK)]

        original = self.request_v3(1, 'sector_b')
        self.wait_for(lambda: track['peer_samples'].get(identity) == (1, motion_digest(original)))
        self.assertIsNone(self.request_v3(2, 'sector_c'))
        self.recipients['sector_c'] = member
        self.request_v3(3, 'sector_c', ('12', '0', '0'))
        self.wait_for(lambda: track['reentries'][identity]['phase'] == 'prepared')
        self.assertEqual(track['peer_frames'][identity], 'sector_b')
        phase, previous_sector, prepared = self.game_callback.calls[0]
        self.assertEqual((phase, previous_sector, prepared['sequence'], prepared['sector_macro']),
                         ('prepare', 'sector_b', 3, 'sector_c'))
        self.assertIsNone(prepared['real_velocity'])

        del self.recipients['sector_c']
        self.assertIsNone(self.request_v3(4, 'sector_c'))
        self.wait_for(self.control._motion_idle)
        self.assertEqual(self.game_callback.calls[1:], [('cancel', 'sector_b', prepared)])
        self.assertEqual(track['peer_frames'][identity], 'sector_b')
        self.assertEqual(track['peer_samples'][identity], (1, motion_digest(original)))

        self.recipients['sector_c'] = member
        self.request_v3(5, 'sector_c')
        self.wait_for(lambda: track['reentries'][identity]['phase'] == 'prepared')
        _, _, prepared_again = self.game_callback.calls[2]
        self.assertEqual(prepared_again['sequence'], 5)
        fresh = self.request_v3(6, 'sector_c', ('32', '0', '0'))
        self.wait_for(lambda: track['peer_samples'].get(identity) == (6, motion_digest(fresh)))
        self.assertEqual(self.game_callback.calls[3:], [('align', 'sector_b', prepared_again, fresh)])
        self.assertEqual(fresh['real_velocity'], {'x': 32.0, 'y': 0.0, 'z': 0.0})
        self.assertEqual(track['peer_frames'][identity], 'sector_c')
        self.assertEqual([sample for handle, sample, _ in self.received if handle == 2], [original])

        next_sample = self.request_v3(7, 'sector_c', ('33', '0', '0'))
        self.wait_for(lambda: track['peer_samples'].get(identity) == (7, motion_digest(next_sample)))
        self.assertEqual([sample for handle, sample, _ in self.received if handle == 2],
                         [original, next_sample])


class EnhancedNpc870ReacquisitionLoopback(unittest.TestCase):
    """ENH-870 (registry index 1318) loopback reentry with synthetic game callbacks."""
    allow_sector_reentry = True
    epoch = '4ba7be438ebb04ec5bb85fdb95b5a488'
    sector_a_track = 10055
    sector_b_track = 1318
    saved_baseline = 10082
    origin_sector = 'cluster_06_sector001_macro'
    other_sector = 'cluster_01_sector001_macro'
    setUp = SourceLatestTwoSectors.setUp
    wait_for = SourceLatestTwoSectors.wait_for
    _begin_pull = SourceLatestTwoSectors._begin_pull
    start_slow_participant = SourceLatestReentryLoopback.start_slow_participant

    def pull_v3(self, index, sequence, sector, source_time, capture_time, pos, rate):
        self._begin_pull()
        request = self.remote.recv(769).decode('ascii').split('|')[-1]
        self.remote.send('|'.join([
            'S', '1' * 32, '2' * 32, 'SAMPLE3', self.epoch, GENERATION,
            request, str(index), str(sequence), sector, str(source_time), str(capture_time),
            str(pos[0]), str(pos[1]), str(pos[2]), *rate,
        ]).encode('ascii'))
        complete, value = self.bridge.poll_latest()
        self.assertTrue(complete)
        return value

    def test_enh_870_reacquisition_cancel_and_fresh_alignment_with_other_observer(self):
        target_sector = 'cluster_14_sector001_macro'
        observer_a = self.recipients[self.other_sector]
        observer_b = self.recipients[self.origin_sector]
        identity_a = (observer_a['id'], observer_a['incarnation'])
        identity_b = (observer_b['id'], observer_b['incarnation'])
        enh_track = self.motion._tracks[(self.epoch, GENERATION, self.sector_b_track)]
        other_track = self.motion._tracks[(self.epoch, GENERATION, self.sector_a_track)]

        # 1. Record ENH-870 (index 1318) initial identity and pose in cluster_06_sector001_macro.
        initial = self.pull_v3(1318, 1, self.origin_sector, 100.0, 10.0,
                               (1000.0, 20.0, -50.0), ('-', '-', '-'))
        self.wait_for(lambda: enh_track['peer_samples'].get(identity_b) == (1, motion_digest(initial)))
        self.assertEqual(enh_track['peer_frames'][identity_b], self.origin_sector)

        # 2. ENH-870 changes sector while observer B has lost view (positive exclusion).
        self.assertIsNone(self.pull_v3(1318, 2, target_sector, 102.0, 12.0,
                                       (5000.0, 100.0, -200.0), ('-', '-', '-')))
        # Observer A continues receiving its latest samples uninterrupted.
        other_1 = self.pull_v3(self.sector_a_track, 1, self.other_sector, 102.5, 12.5,
                               (10.0, 20.0, 30.0), ('15', '0', '0'))
        self.wait_for(lambda: other_track['peer_samples'].get(identity_a) == (1, motion_digest(other_1)))

        # 3. Observer B re-sights ENH-870 -> prepare (rate stripped, frame still origin_sector).
        self.recipients[target_sector] = observer_b
        self.pull_v3(1318, 3, target_sector, 103.0, 13.0,
                     (5120.0, 100.0, -200.0), ('120', '0', '0'))
        self.wait_for(lambda: enh_track['reentries'][identity_b]['phase'] == 'prepared')
        phase, previous_sector, prepared_first = self.game_callback.calls[0]
        self.assertEqual((phase, previous_sector, prepared_first['index'], prepared_first['epoch'],
                          prepared_first['sequence'], prepared_first['sector_macro']),
                         ('prepare', self.origin_sector, 1318, self.epoch, 3, target_sector))
        self.assertIsNone(prepared_first['real_velocity'])
        self.assertEqual(enh_track['peer_frames'][identity_b], self.origin_sector)

        # 4. View disappears during preparation -> cancel without disconnect or frame write.
        del self.recipients[target_sector]
        self.assertIsNone(self.pull_v3(1318, 4, target_sector, 104.0, 14.0,
                                       (5240.0, 100.0, -200.0), ('120', '0', '0')))
        self.wait_for(self.control._motion_idle)
        self.assertEqual(self.game_callback.calls[1], ('cancel', self.origin_sector, prepared_first))
        self.assertEqual(enh_track['peer_frames'][identity_b], self.origin_sector)
        self.assertEqual(enh_track['peer_samples'][identity_b], (1, motion_digest(initial)))

        # Observer A still receives uninterrupted updates throughout cancellation.
        other_2 = self.pull_v3(self.sector_a_track, 2, self.other_sector, 104.5, 14.5,
                               (40.0, 20.0, 30.0), ('15', '0', '0'))
        self.wait_for(lambda: other_track['peer_samples'].get(identity_a) == (2, motion_digest(other_2)))

        # 5. ENH-870 returns to view -> second prepare, then fresh post-prepare alignment with real velocity.
        self.recipients[target_sector] = observer_b
        self.pull_v3(1318, 5, target_sector, 105.0, 15.0,
                     (5360.0, 100.0, -200.0), ('120', '0', '0'))
        self.wait_for(lambda: enh_track['reentries'][identity_b]['phase'] == 'prepared')
        _, _, prepared_second = self.game_callback.calls[2]
        self.assertEqual((prepared_second['sequence'], prepared_second['real_velocity']), (5, None))

        fresh = self.pull_v3(1318, 6, target_sector, 106.0, 16.0,
                             (5480.0, 100.0, -200.0), ('120', '0', '0'))
        self.wait_for(lambda: enh_track['peer_samples'].get(identity_b) == (6, motion_digest(fresh)))
        self.assertEqual(self.game_callback.calls[3:], [('align', self.origin_sector, prepared_second, fresh)])
        self.assertEqual(fresh['real_velocity'], {'x': 120.0, 'y': 0.0, 'z': 0.0})
        self.assertEqual((fresh['x'], fresh['y'], fresh['z']), (5480.0, 100.0, -200.0))
        self.assertEqual(enh_track['peer_frames'][identity_b], target_sector)

        # 6. Normal latest snapshots resume in the destination sector while observer A keeps moving.
        continued = self.pull_v3(1318, 7, target_sector, 107.0, 17.0,
                                 (5600.0, 100.0, -200.0), ('120', '0', '0'))
        self.wait_for(lambda: enh_track['peer_samples'].get(identity_b) == (7, motion_digest(continued)))
        other_3 = self.pull_v3(self.sector_a_track, 3, self.other_sector, 107.5, 17.5,
                               (85.0, 20.0, 30.0), ('15', '0', '0'))
        self.wait_for(lambda: other_track['peer_samples'].get(identity_a) == (3, motion_digest(other_3)))
        self.assertEqual([s['sequence'] for h, s, _ in self.received if h == 2 and s['index'] == 1318],
                         [1, 7])
        self.assertEqual([s['sequence'] for h, s, _ in self.received if h == 1 and s['index'] == self.sector_a_track],
                         [1, 2, 3])


if __name__ == '__main__':
    unittest.main()
