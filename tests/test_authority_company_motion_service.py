"""Real loopback movement sockets with explicit admission and visibility."""
import json
import socket
import unittest
from unittest.mock import patch

from authority.company_motion_frames import MAX_PEERS
from authority.company_motion_service import CompanyMotionService, MAX_SAMPLE_AGE


class CompanyMotionServiceTests(unittest.TestCase):
    def setUp(self):
        self.application_drops = {}
        self.service = CompanyMotionService(application_drops=self.application_drops)
        self.addCleanup(self.service.close)
        self.first = self.grant(1)
        self.second = self.grant(2)

    def grant(self, index):
        return self.service.admit(index, f'{index:032x}', index, 'sector_a', 'a' * 32, 'b' * 32)

    def open(self):
        connection = socket.create_connection(('127.0.0.1', self.service.port), timeout=1)
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.addCleanup(connection.close)
        stream = connection.makefile('rb')
        self.addCleanup(stream.close)
        return connection, stream

    def send(self, connection, message):
        connection.sendall(json.dumps(message).encode() + b'\n')
        self.service.poll()

    def bind(self, grant, **capabilities):
        connection, stream = self.open()
        self.send(connection, {'kind': 'bind_motion', 'ticket': grant['ticket'], **capabilities})
        reply = json.loads(stream.readline())
        self.assertEqual(reply['kind'], 'motion_bound')
        for key, value in capabilities.items():
            self.assertIs(reply[key], value)
        return connection, stream

    def sample(self, grant, sequence, sector='sector_a', velocity=None):
        return {'version': 3, 'epoch': grant['epoch'], 'generation': grant['generation'],
                'index': grant['index'], 'sequence': sequence, 'sector_macro': sector,
                'source_time': sequence / 2, 'capture_time': float(sequence),
                'x': sequence * 10, 'y': 100, 'z': 0, 'real_velocity': velocity}

    def publish(self, source, grant, seq, **kwargs):
        self.send(source, {'kind': 'motion_sample', 'window': self.service.window,
                           'sample': self.sample(grant, seq, **kwargs)})

    def view(self, viewer, indices, sector='sector_a', view_id=1):
        self.send(viewer, {'kind': 'motion_view', 'window': self.service.window,
                           'view_id': view_id, 'indices': indices, 'sector_macro': sector})

    def test_latest_visible_sample_waits_for_previous_receipt(self):
        self.service.set_running(True)
        source, _ = self.bind(self.first)
        viewer, stream = self.bind(self.second)
        self.view(viewer, [1, 2])
        self.publish(source, self.first, 1)
        self.service.poll()
        first = json.loads(stream.readline())
        self.assertEqual(first['sample']['index'], 1)
        self.assertEqual(first['view_id'], 1)
        for seq in range(2, 21):
            self.publish(source, self.first, seq, velocity={'x': 10, 'y': 0, 'z': 0})
        self.send(viewer, {'kind': 'motion_received', 'window': 1, 'view_id': 1, 'index': 1,
                           'generation': self.first['generation'], 'sequence': 1})
        latest = json.loads(stream.readline())['sample']
        self.assertEqual((latest['sequence'], latest['real_velocity']['x']), (20, 10))
        self.assertEqual(self.service.accepted_samples, 20)
        self.publish(source, self.first, 21, sector='sector_b')
        self.service.poll()
        self.assertEqual(json.loads(stream.readline())['kind'], 'motion_end')
        self.service.revoke(2)
        traffic = self.service.participant_traffic()
        publisher, = traffic['active']
        observer, = traffic['recently_closed']
        self.assertEqual((publisher['ship_index'], publisher['channel_bindings'],
                          publisher['samples_accepted'], publisher['samples_with_velocity'],
                          publisher['last_source_sequence']), (1, 1, 21, 19, 21))
        self.assertEqual((observer['ship_index'], observer['view_updates'],
                          observer['nonempty_view_updates'], observer['snapshot_receipts'],
                          observer['last_received_ship_index']), (2, 1, 1, 1, 1))
        self.assertEqual((observer['samples_accepted'], publisher['snapshot_receipts']), (0, 0))
        self.assertEqual(traffic['older_grants_omitted'], 0)
        self.assertNotIn(self.second['ticket'], json.dumps(traffic))
        self.assertNotIn(self.first['generation'], json.dumps(traffic))

    def test_unobserved_sector_and_stale_window_are_fenced(self):
        self.service.set_running(True)
        source, _ = self.bind(self.first)
        viewer, stream = self.bind(self.second)
        self.view(viewer, [1], sector='sector_b')
        self.publish(source, self.first, 1)
        self.service.set_running(False)
        self.service.poll()
        self.assertEqual(json.loads(stream.readline()), {'kind': 'motion_state', 'window': 2, 'running': False})
        self.service.set_running(True)
        self.service.poll()
        self.assertEqual(json.loads(stream.readline())['window'], 3)
        self.send(source, {'kind': 'motion_sample', 'window': 1, 'sample': self.sample(self.first, 2)})
        self.assertEqual(self.service.accepted_samples, 1)
        self.publish(source, self.first, 3, sector='sector_b')
        self.view(viewer, [1], sector='sector_b')
        self.assertEqual(json.loads(stream.readline())['sample']['sequence'], 3)

    def test_only_one_socket_and_the_selected_ship_can_use_a_grant(self):
        self.service.set_running(True)
        source, stream = self.bind(self.first)
        duplicate, duplicate_stream = self.open()
        self.send(duplicate, {'kind': 'bind_motion', 'ticket': self.first['ticket']})
        self.assertEqual(duplicate_stream.readline(), b'')
        self.publish(source, self.second, 1)
        self.assertEqual(stream.readline(), b'')
        self.assertEqual(self.service.accepted_samples, 0)
        rebound, rebound_stream = self.bind(self.first)
        self.service.revoke(1)
        self.assertEqual(rebound_stream.readline(), b'')
        late, late_stream = self.open()
        self.send(late, {'kind': 'bind_motion', 'ticket': self.first['ticket']})
        self.assertEqual(late_stream.readline(), b'')

    def test_traffic_retains_active_grants_and_only_a_bounded_departure_history(self):
        self.service.revoke(2)
        for index in range(3, MAX_PEERS + 5):
            self.grant(index)
            self.service.transport_lost(index)
        traffic = self.service.participant_traffic()
        self.assertEqual([row['ship_index'] for row in traffic['active']], [1])
        self.assertEqual(len(traffic['recently_closed']), MAX_PEERS)
        self.assertEqual(traffic['older_grants_omitted'], 3)
        self.assertEqual(traffic['recently_closed'][-1]['channel_bindings'], 0)

    def test_outdated_last_position_expires_without_reporting_an_unseen_ship(self):
        self.service.set_running(True)
        source, _ = self.bind(self.first)
        viewer, stream = self.bind(self.second)
        self.view(viewer, [1], sector='sector_a')
        with patch('authority.company_motion_service.time.monotonic', return_value=100.0):
            self.publish(source, self.first, 1)
            self.service.poll()
            self.assertEqual(json.loads(stream.readline())['sample']['sequence'], 1)
        with patch('authority.company_motion_service.time.monotonic', return_value=105.0):
            self.service.poll()
            end = json.loads(stream.readline())
            self.assertEqual((end['kind'], end['index'], end['view_id']), ('motion_end', 1, 1))
            self.view(viewer, [1], sector='sector_b', view_id=2)
            self.service.revoke(1)
            self.service.set_running(False)
            self.service.poll()
            self.assertEqual(json.loads(stream.readline())['kind'], 'motion_state')

    def test_opt_in_route_survives_stale_pose_until_view_or_revoke(self):
        self.service.set_running(True)
        source, _ = self.bind(self.first)
        third = self.grant(3)
        viewers = [self.bind(grant, loss_notifications=True, trial_index=1)
                   for grant in (self.second, third)]
        for viewer, _ in viewers:
            self.view(viewer, [1])
        clock_path = 'authority.company_motion_service.time.monotonic'
        with patch(clock_path, return_value=100):
            self.publish(source, self.first, 1)
            for _, stream in viewers:
                self.assertEqual(json.loads(stream.readline())['sample']['sequence'], 1)
        with patch(clock_path, return_value=103):
            self.service.poll()
            for _, stream in viewers:
                self.assertEqual(json.loads(stream.readline())['kind'], 'motion_hidden')
            self.view(viewers[0][0], [], view_id=2)
        self.assertIsNone(self.service._grants[2].trial_route)
        with patch(clock_path, return_value=200):
            self.service.poll()
            self.assertIsNotNone(self.service._grants[3].trial_route)
            self.service.revoke(1)
            self.service.set_running(False)
            self.service.poll()
        self.assertEqual(json.loads(viewers[0][1].readline())['kind'], 'motion_state')
        self.assertEqual(json.loads(viewers[1][1].readline())['kind'], 'motion_end')
        self.assertEqual(json.loads(viewers[1][1].readline())['kind'], 'motion_state')
        self.assertIsNone(self.service._grants[3].trial_route)

    def test_protocol_error_before_rebound_publisher_sample_hard_ends_hidden_route(self):
        self.service.set_running(True)
        source, _ = self.bind(self.first)
        viewer, stream = self.bind(self.second, loss_notifications=True, trial_index=1)
        self.view(viewer, [1])
        self.publish(source, self.first, 1)
        self.assertEqual(json.loads(stream.readline())['kind'], 'motion_snapshot')

        source.shutdown(socket.SHUT_RDWR)
        self.service.poll()
        self.assertEqual(json.loads(stream.readline())['kind'], 'motion_hidden')
        rebound, rebound_stream = self.bind(self.first)
        self.send(rebound, {'kind': 'motion_sample', 'window': self.service.window, 'sample': {}})
        self.assertEqual(rebound_stream.readline(), b'')
        self.service.poll()
        self.assertEqual(json.loads(stream.readline())['kind'], 'motion_end')
        self.assertIsNone(self.service._grants[2].trial_route)

    def test_control_transport_loss_removes_authority_and_regrant_ends_trial_route(self):
        self.service.set_running(True)
        source, source_stream = self.bind(self.first)
        trial, trial_stream = self.bind(self.second, loss_notifications=True, trial_index=1)
        legacy, legacy_stream = self.bind(self.grant(3))
        self.view(trial, [1])
        self.view(legacy, [1])
        self.publish(source, self.first, 1)
        self.assertEqual(json.loads(source_stream.readline())['kind'], 'motion_sampled')
        for stream in (trial_stream, legacy_stream):
            self.assertEqual(json.loads(stream.readline())['kind'], 'motion_snapshot')

        self.service.transport_lost(1)
        self.service.poll()
        self.assertEqual(source_stream.readline(), b'')
        self.assertNotIn(1, self.service._grants)
        stale, stale_stream = self.open()
        self.send(stale, {'kind': 'bind_motion', 'ticket': self.first['ticket']})
        self.assertEqual(stale_stream.readline(), b'')
        self.assertEqual(json.loads(trial_stream.readline())['kind'], 'motion_hidden')
        self.assertEqual(json.loads(legacy_stream.readline())['kind'], 'motion_end')
        self.assertIsNotNone(self.service._grants[2].trial_route)

        replacement = self.grant(1)
        self.assertNotEqual(replacement['generation'], self.first['generation'])
        self.service.poll()
        self.assertEqual(json.loads(trial_stream.readline())['kind'], 'motion_end')
        self.assertIsNone(self.service._grants[2].trial_route)
        self.assertEqual(self.service.accepted_samples, 1)

    def test_revoke_after_resume_verdict_but_before_view_still_prioritizes_hard_end(self):
        self.service.set_running(True)
        source, _ = self.bind(self.first)
        viewer, stream = self.bind(self.second, loss_notifications=True, trial_index=1)
        self.view(viewer, [1])
        self.publish(source, self.first, 1)
        self.assertEqual(json.loads(stream.readline())['kind'], 'motion_snapshot')
        viewer.shutdown(socket.SHUT_RDWR)
        self.service.poll()
        resumed, reply = self.open()
        self.send(resumed, {'kind': 'bind_motion', 'ticket': self.second['ticket'], 'loss_notifications': True,
                            'trial_index': 1, 'resume_route': [1, self.first['generation'], 'sector_a']})
        self.assertIs(json.loads(reply.readline())['resume_valid'], True)
        self.service.revoke(1)
        self.view(resumed, [1])
        self.assertEqual(json.loads(reply.readline())['kind'], 'motion_end')

    def test_control_loss_resume_is_scoped_to_the_prior_observer_and_ends_on_regrant(self):
        self.service.set_running(True)
        source, _ = self.bind(self.first)
        viewer, stream = self.bind(self.second, loss_notifications=True, trial_index=1)
        self.view(viewer, [1])
        self.publish(source, self.first, 1)
        self.assertEqual(json.loads(stream.readline())['kind'], 'motion_snapshot')
        viewer.shutdown(socket.SHUT_RDWR)
        self.service.poll()
        self.service.transport_lost(1)

        claim = [1, self.first['generation'], 'sector_a']
        for grant, expected in ((self.grant(3), False), (self.second, True)):
            resumed, reply = self.open()
            self.send(resumed, {'kind': 'bind_motion', 'ticket': grant['ticket'], 'loss_notifications': True,
                                'trial_index': 1, 'resume_route': claim})
            self.assertIs(json.loads(reply.readline())['resume_valid'], expected)
            self.view(resumed, [1])
        self.grant(1)
        self.service.poll()
        end = json.loads(reply.readline())
        self.assertEqual((end['kind'], end['generation']), ('motion_end', self.first['generation']))

    def test_clock_change_before_binding_sends_only_current_bound_state(self):
        connection, stream = self.open()
        self.service.poll()
        self.service.set_running(True)
        self.send(connection, {'kind': 'bind_motion', 'ticket': self.first['ticket']})
        self.assertEqual(json.loads(stream.readline()), {
            'kind': 'motion_bound', 'window': 1, 'running': True, 'last_sequence': 0,
        })

    def test_application_is_latest_per_ship_without_starving_the_waiting_ship(self):
        self.service.set_running(True)
        fast, fast_stream = self.bind(self.first)
        slow, _ = self.bind(self.second)
        self.publish(fast, self.first, 1)
        self.publish(slow, self.second, 1)
        velocity = {'x': 10, 'y': 0, 'z': 0}
        for sequence in range(2, 5):
            self.publish(fast, self.first, sequence, velocity=velocity)
        for sequence in range(1, 5):
            # TCP acknowledgement requires no application consumer at all.
            self.assertEqual(json.loads(fast_stream.readline()), {
                'kind': 'motion_sampled', 'window': 1, 'sequence': sequence,
            })
        first, = self.service.take_application_batch()
        self.assertEqual((first.handle, first.window, first.sample),
                         (1, 1, self.sample(self.first, 4, velocity=velocity)))
        first.sample['real_velocity']['x'] = -50
        self.assertEqual(first.sample['real_velocity'], velocity)
        self.assertTrue(self.service.is_application_current(first))
        self.publish(fast, self.first, 5, velocity=velocity)
        self.publish(fast, self.first, 6, velocity=velocity)
        second, = self.service.take_application_batch()
        self.assertEqual((second.index, second.sample['sequence']), (2, 1))
        self.assertFalse(self.service.is_application_current(first))
        latest, = self.service.take_application_batch()
        self.assertEqual((latest.index, latest.sample['sequence']), (1, 6))
        self.assertEqual(self.service.take_application_batch(), ())

    def test_only_accepted_sector_changes_invalidate_the_inspected_ship(self):
        self.service.set_running(True)
        source, stream = self.bind(self.first)
        other, _ = self.bind(self.second)
        self.publish(source, self.first, 1, sector='sector_b')
        self.publish(other, self.second, 1)
        initial, unrelated = self.service.take_application_batch(2)
        self.assertEqual(initial.sector_revision, 0)
        self.assertEqual(json.loads(stream.readline())['sequence'], 1)

        velocity = {'x': 10, 'y': 0, 'z': 0}
        self.publish(source, self.first, 2, sector='sector_b', velocity=velocity)
        self.assertEqual(json.loads(stream.readline())['sequence'], 2)
        self.assertTrue(self.service.is_application_current(initial))
        self.publish(source, self.first, 3, sector='sector_a', velocity=velocity)
        self.assertEqual(stream.readline(), b'')
        self.assertTrue(self.service.is_application_current(initial))
        self.assertEqual(self.service.accepted_samples, 3)

        source, _ = self.bind(self.first)
        self.publish(source, self.first, 3, sector='sector_a')
        self.assertFalse(self.service.is_application_current(initial, after_inspect=True))
        self.assertEqual(self.application_drops['last_reason'], 'after_inspect_invalidated')
        self.assertTrue(self.service.is_application_current(unrelated))
        self.publish(source, self.first, 4, sector='sector_a', velocity=velocity)
        changed, = self.service.take_application_batch()
        self.assertEqual((changed.sample['sector_macro'], changed.sample['sequence'], changed.sector_revision),
                         ('sector_a', 4, 1))

        self.publish(source, self.first, 5, sector='sector_b')
        self.assertFalse(self.service.is_application_current(changed))
        self.assertFalse(self.service.is_application_current(initial))
        self.service.revoke(1)
        self.assertFalse(self.service.is_application_current(changed))
        latest, = self.service.freeze_application()
        self.assertEqual((latest.sample['sector_macro'], latest.sector_revision), ('sector_b', 2))
        self.assertTrue(self.service.is_application_current(latest))

    def test_sector_revision_continues_across_motion_windows(self):
        self.service.set_running(True)
        source, _ = self.bind(self.first)
        self.publish(source, self.first, 1)
        self.publish(source, self.first, 2, sector='sector_b')
        frozen, = self.service.freeze_application()
        self.assertEqual(frozen.sector_revision, 1)
        self.assertTrue(self.service.is_application_current(frozen))
        self.service.set_running(True)
        self.publish(source, self.first, 3, sector='sector_b')
        resumed, = self.service.take_application_batch()
        self.assertEqual(resumed.sector_revision, 1)
        self.assertFalse(self.service.is_application_current(frozen))
        self.assertTrue(self.service.is_application_current(resumed))

    def test_freeze_keeps_only_accepted_data_from_the_closed_window(self):
        self.service.set_running(True)
        departed, _ = self.bind(self.first)
        remaining, _ = self.bind(self.second)
        self.publish(departed, self.first, 1)
        self.publish(departed, self.first, 2, sector='sector_b')
        self.publish(remaining, self.second, 1)
        for socket_, grant in ((departed, self.first), (remaining, self.second)):
            socket_.sendall(json.dumps({'kind': 'motion_sample', 'window': 1,
                                        'sample': self.sample(grant, 3)}).encode() + b'\n')
        self.service.revoke(1)
        first, second = self.service.freeze_application()
        self.assertEqual([(record.index, record.sample['sequence'], record.sector_revision)
                          for record in (first, second)], [(1, 2, 1), (2, 1, 0)])
        self.service.poll()
        self.assertEqual(self.service.accepted_samples, 3)
        self.assertFalse(self.service.running)
        self.assertTrue(self.service.is_application_current(first))
        self.assertTrue(self.service.is_application_current(second))
        self.assertEqual(self.service.take_application_batch(), ())
        self.assertEqual(self.service.freeze_application(), ())

        replacement = self.service.admit(3, '3' * 32, 1, 'sector_a', 'a' * 32, 'b' * 32)
        self.assertFalse(self.service.is_application_current(first))
        self.assertTrue(self.service.is_application_current(second))
        self.service.set_running(True)
        self.assertFalse(self.service.is_application_current(second))
        self.assertEqual(self.service.take_application_batch(), ())
        source, stream = self.bind(replacement)
        self.publish(source, replacement, 1)
        current, = self.service.take_application_batch()
        self.assertEqual((current.handle, current.window, current.sample['generation'], current.sector_revision),
                         (3, 3, replacement['generation'], 0))
        self.assertTrue(self.service.is_application_current(current))
        self.assertEqual(json.loads(stream.readline())['sequence'], 1)

    def test_regrant_removes_an_undelivered_retired_sample(self):
        self.service.set_running(True)
        source, _ = self.bind(self.first)
        self.publish(source, self.first, 1)
        self.service.revoke(1)
        self.service.admit(3, '3' * 32, 1, 'sector_a', 'a' * 32, 'b' * 32)
        self.assertEqual(self.service.freeze_application(), ())

    def test_two_accepted_ships_expire_in_the_queue_and_after_inspect(self):
        self.service.set_running(True)
        first, _ = self.bind(self.first)
        second, _ = self.bind(self.second)
        with patch('authority.company_motion_service.time.monotonic', return_value=100.0):
            self.publish(first, self.first, 1)
            self.publish(second, self.second, 7)
            inspected, = self.service.take_application_batch(1)
            self.assertTrue(self.service.is_application_current(inspected, after_inspect=False))
        self.assertEqual(self.service.accepted_samples, 2)
        self.assertEqual(self.application_drops, {'last_reason': None, 'by_reason': {}})
        with patch('authority.company_motion_service.time.monotonic', return_value=100.0 + MAX_SAMPLE_AGE):
            self.assertFalse(self.service.is_application_current(inspected, after_inspect=True))
            self.assertEqual(self.service.freeze_application(), ())
            self.assertEqual(self.service.freeze_application(), ())
        self.assertEqual(self.application_drops['last_reason'], 'queued_expired')
        self.assertEqual(self.application_drops['by_reason'], {
            'after_inspect_expired': {'count': 1, 'last_ship_index': 1, 'last_sequence': 1,
                                      'last_age_seconds': MAX_SAMPLE_AGE},
            'queued_expired': {'count': 1, 'last_ship_index': 2, 'last_sequence': 7,
                               'last_age_seconds': MAX_SAMPLE_AGE},
        })

    def test_reassignment_invalidates_the_inspected_ship_and_queued_successor(self):
        self.service.set_running(True)
        source, _ = self.bind(self.first)
        with patch('authority.company_motion_service.time.monotonic', return_value=100.0):
            self.publish(source, self.first, 1)
            inspected, = self.service.take_application_batch()
            self.publish(source, self.first, 2)
            self.service.revoke(1)
            self.assertTrue(self.service.is_application_current(inspected, after_inspect=False))
        with patch('authority.company_motion_service.time.monotonic', return_value=100.5):
            self.service.admit(3, '3' * 32, 1, 'sector_a', 'a' * 32, 'b' * 32)
            self.assertFalse(self.service.is_application_current(inspected, after_inspect=True))
        self.assertEqual(self.application_drops['last_reason'], 'after_inspect_invalidated')
        self.assertEqual(self.application_drops['by_reason'], {
            'queued_reassigned': {'count': 1, 'last_ship_index': 1, 'last_sequence': 2,
                                  'last_age_seconds': .5},
            'after_inspect_invalidated': {'count': 1, 'last_ship_index': 1, 'last_sequence': 1,
                                          'last_age_seconds': .5},
        })

    def test_old_data_expires_with_server_age_including_frozen_batch(self):
        self.service.set_running(True)
        source, _ = self.bind(self.first)
        with patch('authority.company_motion_service.time.monotonic', return_value=100.0):
            self.publish(source, self.first, 1)
            record, = self.service.take_application_batch()
            self.publish(source, self.first, 2)
        with patch('authority.company_motion_service.time.monotonic', return_value=100.0 + MAX_SAMPLE_AGE):
            self.assertFalse(self.service.is_application_current(record))
            self.assertEqual(self.service.freeze_application(), ())

    def test_reconnect_cannot_reinsert_a_regressed_capture_clock(self):
        self.service.set_running(True)
        source, _ = self.bind(self.first)
        self.publish(source, self.first, 4)
        source.shutdown(socket.SHUT_RDWR)
        self.service.poll()
        reconnect, replies = self.bind(self.first)
        stale = self.sample(self.first, 5)
        stale['capture_time'] = 2.0
        self.send(reconnect, {'kind': 'motion_sample', 'window': 1, 'sample': stale})
        self.assertEqual(replies.readline(), b'')
        self.assertEqual(self.service.accepted_samples, 1)
        accepted, = self.service.take_application_batch()
        self.assertEqual(accepted.sample['sequence'], 4)

    def test_departed_grants_cannot_grow_the_application_queue(self):
        self.service.set_running(True)
        for index in range(3, MAX_PEERS + 6):
            grant = self.grant(index)
            source, _ = self.bind(grant)
            self.publish(source, grant, 1)
            self.service.revoke(index)
        active, _ = self.bind(self.first)
        self.publish(active, self.first, 1)
        batch = self.service.freeze_application()
        self.assertEqual(len(batch), MAX_PEERS)
        self.assertEqual(batch[-1].index, 1)
        self.assertEqual(batch[0].index, 7)
