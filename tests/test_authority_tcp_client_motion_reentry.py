"""Loopback TCP reentry with an injected controller and synthetic poses."""
import json
from pathlib import Path
import socket
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from authority.experimental_tcp_motion import motion_digest
from authority.experimental_tcp_participant_client import ExperimentalTCPParticipantClient
from test_authority_experimental_tcp_participant_client import Controller, command, frame

EPOCH = 'a' * 32
INCARNATION = 'b' * 32
PREVIOUS = 'cluster_a_sector001_macro'
TARGET = 'cluster_b_sector001_macro'


def snapshot(sequence, sector=PREVIOUS, **changes):
    return dict({'version': 3, 'epoch': 'c' * 32, 'generation': 'd' * 32,
                 'index': 12, 'sequence': sequence, 'sector_macro': sector,
                 'source_time': 10, 'capture_time': sequence,
                 'x': 10, 'y': 20, 'z': 30, 'real_velocity': None}, **changes)


def envelope(kind, request_id, **payload):
    return dict({'kind': kind, 'version': 1, 'session_epoch': EPOCH,
                 'incarnation': INCARNATION, 'connection_handle': 1,
                 'request_id': request_id}, **payload)


def prepare(sample):
    return envelope('motion_reentry_prepare', sample['sequence'],
                    previous_sector=PREVIOUS, sample=sample)


def align(sample, prepared_sequence):
    return envelope('motion_reentry_align', sample['sequence'], previous_sector=PREVIOUS,
                    sample=sample, prepared_sequence=prepared_sequence)


def cancel(sample):
    return envelope('motion_reentry_cancel', sample['sequence'], previous_sector=PREVIOUS,
                    prepared_sequence=sample['sequence'], sample_sha256=motion_digest(sample))


class MotionReentryController:
    def __init__(self):
        self.calls = []
        self.result = True

    def prepare(self, previous_sector, sample):
        self.calls.append(('prepare', previous_sector, sample))
        return self.result

    def align(self, previous_sector, prepared_sample, sample):
        self.calls.append(('align', previous_sector, prepared_sample, sample))
        return self.result

    def cancel(self, previous_sector, prepared_sample):
        self.calls.append(('cancel', previous_sector, prepared_sample))
        return self.result


class ClientMotionReentryTest(unittest.TestCase):
    def open(self, *, enabled=True):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            conn = socket.create_connection(listener.getsockname(), timeout=1)
            self.addCleanup(conn.close)
            self.server, _ = listener.accept()
            self.server.settimeout(1)
            self.addCleanup(self.server.close)
        self.controller = MotionReentryController()
        self.consumed = []
        self.client = ExperimentalTCPParticipantClient(
            conn, Controller(), enabled=True, epoch=EPOCH, handle=1,
            apply_motion=self.consume,
            motion_reentry_controller=self.controller if enabled else None,
        )

    def consume(self, sample, despawn):
        self.consumed.append((sample, despawn))
        return True

    def exchange(self, request):
        self.server.sendall(frame(request))
        self.assertFalse(self.client.poll(0.3))
        self.assertTrue(self.client.poll(0.3))
        with self.server.makefile('rb') as reply:
            return json.loads(reply.readline())

    def refuse(self, request, error=ValueError):
        self.server.sendall(frame(request))
        with self.assertRaises(error):
            self.client.poll(0.3)
        self.assertEqual(self.server.recv(1), b'')

    def send_snapshot(self, sample):
        return self.exchange(envelope('motion_snapshot', sample['sequence'], sample=sample))

    def check_receipt(self, request, status):
        reply = {name: value for name, value in request.items() if name != 'sample'}
        if 'sample' in request:
            reply['sample_sha256'] = motion_digest(request['sample'])
        reply.update(kind=f'motion_reentry_{status}_receipt', status=status)
        self.assertEqual(self.exchange(request), reply)

    def test_fresh_alignment_preserves_clocks_and_destination_velocity(self):
        self.open()
        original = snapshot(1)
        target = snapshot(3, TARGET)
        fresh = snapshot(5, TARGET, real_velocity={'x': 2, 'y': 0, 'z': 0})
        self.send_snapshot(original)
        self.check_receipt(prepare(target), 'prepared')
        self.check_receipt(align(fresh, 3), 'aligned')
        self.assertEqual(self.controller.calls, [('prepare', PREVIOUS, target),
                                                 ('align', PREVIOUS, target, fresh)])
        next_sample = snapshot(7, TARGET, real_velocity={'x': 3, 'y': 0, 'z': 0})
        self.send_snapshot(next_sample)
        self.assertEqual(self.consumed, [(original, False), (next_sample, False)])
        stale_frame = snapshot(8)
        self.refuse(envelope('motion_snapshot', 8, sample=stale_frame))
        self.assertEqual(self.consumed, [(original, False), (next_sample, False)])

    def test_cancel_discards_only_preparation_and_a_late_align_fails(self):
        self.open()
        original = snapshot(1)
        target = snapshot(2, TARGET)
        previous = snapshot(3)
        self.send_snapshot(original)
        self.check_receipt(prepare(target), 'prepared')
        self.check_receipt(cancel(target), 'canceled')
        self.send_snapshot(previous)
        self.refuse(align(snapshot(4, TARGET), 2))
        self.assertEqual(self.consumed, [(original, False), (previous, False)])
        self.assertEqual(self.controller.calls, [('prepare', PREVIOUS, target),
                                                 ('cancel', PREVIOUS, target)])

    def test_cancelled_prepare_sequence_cannot_be_replayed(self):
        self.open()
        self.send_snapshot(snapshot(1))
        target = snapshot(2, TARGET)
        self.check_receipt(prepare(target), 'prepared')
        self.check_receipt(cancel(target), 'canceled')
        self.refuse(prepare(target))
        self.assertEqual(len(self.controller.calls), 2)

    def test_new_prepare_cannot_regress_behind_cancelled_capture_time(self):
        self.open()
        self.send_snapshot(snapshot(1))
        target = snapshot(3, TARGET)
        self.check_receipt(prepare(target), 'prepared')
        self.check_receipt(cancel(target), 'canceled')
        self.refuse(prepare(snapshot(5, TARGET, capture_time=2)))
        self.assertEqual(len(self.controller.calls), 2)

    def test_unapproved_alignment_cannot_advance_client_track(self):
        for reason, request in [
            ('stale sequence', align(snapshot(3, TARGET, capture_time=5), 3)),
            ('stale capture', align(snapshot(5, TARGET, capture_time=3), 3)),
            ('game time regression', align(snapshot(5, TARGET, source_time=9), 3)),
            ('foreign epoch', align(snapshot(5, TARGET, epoch='e' * 32), 3)),
            ('foreign generation', align(snapshot(5, TARGET, generation='e' * 32), 3)),
            ('foreign index', align(snapshot(5, TARGET, index=13), 3)),
            ('wrong target', align(snapshot(5, 'cluster_c_sector001_macro'), 3)),
            ('wrong prepare', align(snapshot(5, TARGET), 2)),
            ('foreign peer', dict(align(snapshot(5, TARGET), 3), incarnation='e' * 32)),
            ('wrong cancellation digest', dict(cancel(snapshot(3, TARGET)), sample_sha256='0' * 64)),
            ('direct target snapshot', envelope('motion_snapshot', 5, sample=snapshot(5, TARGET))),
        ]:
            with self.subTest(reason=reason):
                self.open()
                self.send_snapshot(snapshot(1))
                target = snapshot(3, TARGET)
                self.check_receipt(prepare(target), 'prepared')
                self.refuse(request)
                self.assertEqual(self.controller.calls, [('prepare', PREVIOUS, target)])
                self.assertEqual(self.consumed, [(snapshot(1), False)])

    def test_each_callback_must_confirm_and_preserve_its_samples(self):
        for phase in ('prepare', 'align', 'cancel'):
            for result in (False, 1, 'confirmed'):
                with self.subTest(phase=phase, result=result):
                    self.open()
                    self.send_snapshot(snapshot(1))
                    target = snapshot(3, TARGET)
                    requests = dict(prepare=prepare(target), align=align(snapshot(5, TARGET), 3),
                                    cancel=cancel(target))
                    if phase != 'prepare':
                        self.check_receipt(requests['prepare'], 'prepared')
                    self.controller.result = result
                    self.refuse(requests[phase], RuntimeError)
                    self.assertEqual(self.controller.calls[-1][0], phase)
        self.open()
        self.send_snapshot(snapshot(1))
        target = snapshot(3, TARGET)
        self.check_receipt(prepare(target), 'prepared')

        def mutate_prepared(previous, prepared, sample):
            prepared['x'] += 1
            return True

        self.controller.align = mutate_prepared
        self.refuse(align(snapshot(5, TARGET), 3), RuntimeError)

    def test_v2_prepare_needs_unknown_rates_and_cannot_change_version(self):
        rotation = {'yaw': 0, 'pitch': 0, 'roll': 0}

        def full_pose(sequence, sector, **changes):
            sample = snapshot(sequence, sector, source_time=sequence)
            del sample['capture_time']
            del sample['real_velocity']
            return dict(dict(sample, version=2, orientation=rotation, velocity=None, angular_rate=None),
                        **changes)

        self.open()
        self.send_snapshot(full_pose(1, PREVIOUS))
        moving_prepare = full_pose(3, TARGET, velocity={'x': 1, 'y': 0, 'z': 0}, angular_rate=rotation)
        self.refuse(prepare(moving_prepare))
        self.assertEqual(self.controller.calls, [])
        self.open()
        self.send_snapshot(full_pose(1, PREVIOUS))
        self.check_receipt(prepare(full_pose(3, TARGET)), 'prepared')
        self.refuse(align(snapshot(5, TARGET), 3))
        self.assertEqual(len(self.controller.calls), 1)

    def test_default_preserves_legacy_snapshot_delivery_and_refuses_prepare(self):
        self.open(enabled=False)
        self.send_snapshot(snapshot(1))
        self.send_snapshot(snapshot(2, TARGET))
        self.refuse(prepare(snapshot(3)))
        self.assertEqual(self.controller.calls, [])
        self.assertEqual(self.consumed, [(snapshot(1), False), (snapshot(2, TARGET), False)])

    def test_pending_prepare_does_not_survive_participant_control_or_close(self):
        self.open()
        self.send_snapshot(snapshot(1))
        self.check_receipt(prepare(snapshot(3, TARGET)), 'prepared')
        self.refuse(command(), RuntimeError)
        self.assertIsNone(self.client._prepared_reentry)
        self.open()
        self.send_snapshot(snapshot(1))
        self.check_receipt(prepare(snapshot(3, TARGET)), 'prepared')
        self.client.close()
        self.assertIsNone(self.client._prepared_reentry)


if __name__ == '__main__':
    unittest.main()
