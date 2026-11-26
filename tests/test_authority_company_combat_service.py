"""Exercise real loopback framing with a synthetic native damage adapter."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import json
import select
import socket
import time
import unittest
from unittest.mock import Mock, patch

from authority.company_combat_client import CompanyCombatClient
from authority.company_combat_frames import MAX_BATCH, encode_frame
from authority.company_combat_service import (CombatImpactRejected, CompanyCombatService,
                                              derive_combat_ticket)


def hit(sequence, target=1074, amount=26):
    return {'id': sequence, 'target_index': target, 'amount': amount}


class CompanyCombatTests(unittest.TestCase):
    def setUp(self):
        self.grants = {}
        self.first, self.second = self.grant(1073), self.grant(943)
        self.health = {1074: (30, 3600), 943: (100, 1000)}
        self.calls = []
        self.service = CompanyCombatService(self.grants.get, self.apply)
        self.addCleanup(self.service.close)

    def grant(self, index):
        ticket = f'{index:064x}'
        generation = f'{index:032x}'
        self.grants[derive_combat_ticket(ticket)] = (generation, 'b' * 32, index)
        return dict(ticket=ticket, generation=generation, epoch='b' * 32, index=index,
                    session_epoch='a' * 32)

    def apply(self, binding, target, amount):
        self.calls.append((binding[2], target, amount))
        shield, hull = self.health[target]
        result = max(shield - amount, 0), hull - max(amount - shield, 0)
        self.health[target] = result
        return result

    def open(self):
        connection = socket.create_connection(('127.0.0.1', self.service.port), timeout=1)
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.addCleanup(connection.close)
        stream = connection.makefile('rb')
        self.addCleanup(stream.close)
        return connection, stream

    def frame(self, credential, impacts, **fields):
        message = dict(kind='combat_impacts', ticket=derive_combat_ticket(credential['ticket']),
                       generation=credential['generation'], registry_epoch=credential['epoch'],
                       shooter_index=credential['index'], impacts=impacts)
        message.update(fields)
        return encode_frame(message)

    def receive(self, stream):
        return json.loads(stream.readline())['receipts']

    def health_request(self, credential, window, sequence):
        request = json.loads(self.frame(credential, []))
        del request['impacts']
        request.update(kind='combat_health_read', window=window, sequence=sequence, after_revision=None)
        return encode_frame(request)

    def test_pending_native_health_discarded_when_the_world_pauses(self):
        self.service.close()
        operation = Mock()
        operation.poll.return_value = None
        window = 3
        self.service = CompanyCombatService(self.grants.get, self.apply,
                                           begin_health_read=lambda binding: operation,
                                           health_window=lambda binding: window)
        self.addCleanup(self.service.close)
        connection, stream = self.open()
        connection.sendall(self.health_request(self.first, window, 1))
        self.service.poll()
        operation.poll.assert_called_once_with()

        window = None
        operation.poll.return_value = dict(shield=10, maxshield=100, hull=20, maxhull=100)
        self.service.poll()
        response = json.loads(stream.readline())
        self.assertIsNone(response['health'])
        self.assertIsNone(response['revision'])
        operation.cancel.assert_called_once_with()
        operation.poll.assert_called_once_with()
        self.assertFalse(self.calls)

    def test_pending_impact_discards_an_older_native_health_read(self):
        self.service.close()
        old_read = Mock(poll=Mock(return_value=None))
        hit_write = Mock(poll=Mock(return_value=None))
        new_health = dict(shield=4, maxshield=2366, hull=3600, maxhull=3600)
        new_read = Mock(poll=Mock(return_value=new_health))
        begin_read = Mock(side_effect=(old_read, new_read))
        begin_impact = Mock(return_value=hit_write)
        self.service = CompanyCombatService(self.grants.get, begin_impact,
                                           begin_health_read=begin_read, health_window=lambda binding: 3)
        self.addCleanup(self.service.close)
        victim_grant = self.grant(1074)
        victim, victim_reply = self.open()
        victim.sendall(self.health_request(victim_grant, 3, 1))
        self.service.poll()
        old_read.poll.assert_called_once_with()

        old_read.poll.return_value = dict(new_health, shield=30)
        attacker, attacker_reply = self.open()
        attacker.sendall(self.frame(self.first, [hit(1)]))
        self.service.poll()
        self.assertTrue(self.service.native_pending)
        old_read.poll.assert_called_once_with()
        old_read.cancel.assert_called_once_with()
        stale_response = json.loads(victim_reply.readline())
        self.assertIsNone(stale_response['health'])
        self.assertIsNone(stale_response['revision'])

        victim.sendall(self.health_request(victim_grant, 3, 2))
        self.service.poll()
        begin_read.assert_called_once()
        hit_write.poll.return_value = (4, 3600)
        self.assertTrue(self.service.settle_pending())
        self.service.poll()
        self.assertEqual(self.receive(attacker_reply)[0]['order'], 1)
        fresh_response = json.loads(victim_reply.readline())
        self.assertEqual(fresh_response['health'], new_health)
        self.assertEqual(fresh_response['revision'], 1)
        begin_impact.assert_called_once()

    def test_fifo_batches_multi_reporter_health_and_retry_versions(self):
        second, second_reply = self.open()
        first, first_reply = self.open()
        original = self.frame(self.first, [hit(1), hit(2, 943, 10)])
        first.sendall(original)
        self.service.poll()
        self.assertTrue(self.service.pending)
        self.assertEqual(self.calls, [(1073, 1074, 26)])
        second.sendall(self.frame(self.second, [hit(1)]))
        self.service.poll()
        self.service.poll()
        receipts = self.receive(first_reply)
        self.assertEqual([(row['order'], row['shield'], row['hull']) for row in receipts],
                         [(1, 4, 3600), (2, 90, 1000)])
        self.assertEqual(self.receive(second_reply),
                         [dict(hit(1), order=3, status='confirmed', shield=0, hull=3578)])

        repeat, repeat_reply = self.open()
        repeat.sendall(original)
        self.service.poll()
        self.assertEqual(self.receive(repeat_reply), receipts)
        self.assertEqual(self.calls, [(1073, 1074, 26), (1073, 943, 10), (943, 1074, 26)])
        self.assertEqual(self.health[1074], (0, 3578))

    def test_changed_retry_and_expired_retry_do_not_apply_new_suffix(self):
        with patch('authority.company_combat_service.RECEIPT_HISTORY', 2):
            first, reply = self.open()
            first.sendall(self.frame(self.first, [hit(1), hit(2), hit(3)]))
            for _ in range(3):
                self.service.poll()
            self.assertEqual(len(self.receive(reply)), 3)

            first.sendall(self.frame(self.first, [hit(2), hit(3, amount=25), hit(4)]))
            self.service.poll()
            self.assertEqual(reply.readline(), b'')
            again, old_reply = self.open()
            again.sendall(self.frame(self.first, [hit(1)]))
            self.service.poll()
            self.assertEqual(old_reply.readline(), b'')
            self.assertEqual(len(self.calls), 3)

            latest, latest_reply = self.open()
            latest.sendall(self.frame(self.first, [hit(3), hit(4)]))
            self.service.poll()
            self.assertEqual([row['order'] for row in self.receive(latest_reply)], [3, 4])
            self.assertEqual(len(self.calls), 4)

    def test_bad_frames_cannot_write_and_partial_peer_does_not_block(self):
        incomplete, _ = self.open()
        incomplete.sendall(self.frame(self.first, [hit(1)])[:20])
        malformed = (
            self.frame(self.first, [hit(1)], ticket=self.first['ticket']),
            self.frame(self.first, [hit(1)], registry_epoch='a' * 32),
            self.frame(self.first, [hit(1)], generation='f' * 32),
            self.frame(self.first, [hit(1)], shooter_index=943),
            self.frame(self.first, [hit(1, 1073)]),
            self.frame(self.first, [hit(1, amount=True)]),
            self.frame(self.first, [hit(2)]),
            self.frame(self.first, [hit(sequence) for sequence in range(1, MAX_BATCH + 2)]),
        )
        for frame in malformed:
            connection, reply = self.open()
            connection.sendall(frame)
            self.service.poll()
            self.assertEqual(reply.readline(), b'')
        self.assertFalse(self.calls)
        healthy, reply = self.open()
        healthy.sendall(self.frame(self.second, [hit(1)]))
        self.service.poll()
        self.assertEqual(self.receive(reply)[0]['status'], 'confirmed')

    def test_revoke_admission_between_impacts(self):
        first, reply = self.open()
        first.sendall(self.frame(self.first, [hit(1), hit(2)]))
        self.service.poll()
        del self.grants[derive_combat_ticket(self.first['ticket'])]
        self.service.poll()
        self.assertEqual(reply.readline(), b'')
        self.assertEqual(self.calls, [(1073, 1074, 26)])
        retry, retry_reply = self.open()
        retry.sendall(self.frame(self.first, [hit(1)]))
        self.service.poll()
        self.assertEqual(retry_reply.readline(), b'')

    def test_no_adapter_and_certain_refusal_leave_writes_closed_or_available(self):
        self.service.close()
        adapter = Mock(side_effect=(CombatImpactRejected(), (4, 3600)))
        service = self.service = CompanyCombatService(self.grants.get, adapter)
        self.addCleanup(service.close)
        first, reply = self.open()
        first.sendall(self.frame(self.first, [hit(1), hit(2)]))
        service.poll()
        service.poll()
        self.assertEqual([row['status'] for row in self.receive(reply)], ['rejected', 'confirmed'])
        self.assertEqual(adapter.call_count, 2)
        self.assertFalse(service.poisoned)

        service.close()
        service = self.service = CompanyCombatService(self.grants.get, None)
        self.addCleanup(service.close)
        first, reply = self.open()
        first.sendall(self.frame(self.first, [hit(1)]))
        service.poll()
        self.assertEqual(self.receive(reply),
                         [dict(hit(1), order=1, status='rejected', shield=None, hull=None)])
        self.assertFalse(service.poisoned)

    def test_uncertain_native_outcome_is_cached_and_poisoned(self):
        self.service.close()
        adapter = Mock(side_effect=TimeoutError('native receipt lost'))
        service = self.service = CompanyCombatService(self.grants.get, adapter)
        self.addCleanup(service.close)
        first, reply = self.open()
        frame = self.frame(self.first, [hit(1), hit(2)])
        first.sendall(frame)
        with self.assertLogs('authority.company_combat_service', level='ERROR'):
            service.poll()
        service.poll()
        receipts = self.receive(reply)
        self.assertEqual([(row['status'], row['shield'], row['hull']) for row in receipts],
                         [('unknown', None, None), ('rejected', None, None)])
        self.assertTrue(service.poisoned)

        first.sendall(frame)
        service.poll()
        self.assertEqual(self.receive(reply), receipts)
        other, other_reply = self.open()
        other.sendall(self.frame(self.second, [hit(1)]))
        service.poll()
        self.assertEqual(self.receive(other_reply)[0]['status'], 'rejected')
        self.assertEqual(adapter.call_count, 1)

    def test_deferred_receipt_survives_disconnect_and_serializes_later_writes(self):
        self.service.close()
        operation = Mock()
        operation.poll.return_value = None
        begin = Mock(side_effect=(operation, (0, 3578)))
        service = self.service = CompanyCombatService(self.grants.get, begin)
        self.addCleanup(service.close)
        original, original_reply = self.open()
        frame = self.frame(self.first, [hit(1)])
        original.sendall(frame)
        service.poll()
        self.assertTrue(service.native_pending)
        original_reply.close()
        original.close()

        retry, reply = self.open()
        second, second_reply = self.open()
        retry.sendall(frame)
        second.sendall(self.frame(self.second, [hit(1)]))
        service.poll()
        self.assertFalse(select.select([retry, second], [], [], 0)[0])
        self.assertEqual(begin.call_count, 1)

        operation.poll.return_value = (4, 3600)
        self.assertTrue(service.settle_pending())
        self.assertFalse(service.native_pending)
        self.assertEqual(begin.call_count, 1)
        for _ in range(3):
            service.poll()
        self.assertEqual(self.receive(reply),
                         [dict(hit(1), order=1, status='confirmed', shield=4, hull=3600)])
        self.assertEqual(self.receive(second_reply),
                         [dict(hit(1), order=2, status='confirmed', shield=0, hull=3578)])
        self.assertEqual(begin.call_count, 2)

    def test_deferred_revocation_settles_the_dispatched_command_without_a_new_one(self):
        self.service.close()
        operation = Mock()
        operation.poll.side_effect = (None, (4, 3600))
        begin = Mock(side_effect=(operation, (0, 3578)))
        service = self.service = CompanyCombatService(self.grants.get, begin)
        self.addCleanup(service.close)
        connection, reply = self.open()
        connection.sendall(self.frame(self.first, [hit(1), hit(2)]))
        service.poll()
        del self.grants[derive_combat_ticket(self.first['ticket'])]

        self.assertFalse(service.settle_pending())
        self.assertTrue(service.settle_pending())
        self.assertEqual(operation.poll.call_count, 2)
        self.assertEqual(begin.call_count, 1)
        service.poll()
        self.assertEqual(reply.readline(), b'')
        second, second_reply = self.open()
        second.sendall(self.frame(self.second, [hit(1)]))
        service.poll()
        self.assertEqual(self.receive(second_reply)[0]['order'], 2)
        self.assertEqual(begin.call_count, 2)

    def test_deferred_timeout_only_poisoned_after_provider_reports_uncertainty(self):
        self.service.close()
        operation = Mock()
        operation.poll.side_effect = (None, TimeoutError('native receipt lost'))
        begin = Mock(return_value=operation)
        service = self.service = CompanyCombatService(self.grants.get, begin)
        self.addCleanup(service.close)
        connection, reply = self.open()
        frame = self.frame(self.first, [hit(1), hit(2)])
        connection.sendall(frame)
        service.poll()
        service.poll()
        self.assertTrue(service.native_pending)
        self.assertFalse(service.poisoned)
        self.assertFalse(select.select([connection], [], [], 0)[0])
        with self.assertLogs('authority.company_combat_service', level='ERROR'):
            service.poll()
        self.assertTrue(service.poisoned)
        self.assertFalse(service.native_pending)
        service.poll()
        receipts = self.receive(reply)
        self.assertEqual([row['status'] for row in receipts], ['unknown', 'rejected'])
        connection.sendall(frame)
        service.poll()
        self.assertEqual(self.receive(reply), receipts)
        self.assertEqual(begin.call_count, 1)

    def grouped_service(self, single, group, **options):
        self.service.close()
        self.service = CompanyCombatService(self.grants.get, single, apply_impacts=group, **options)
        self.addCleanup(self.service.close)

    def test_native_group_reserves_order_and_skips_a_cached_prefix_on_retry(self):
        group = Mock(side_effect=[((4, 3600), (0, 3578)), ((74, 1000), (0, 3552))])
        self.grouped_service(self.apply, group)
        peer, reply = self.open()
        first = [hit(1), hit(2)]
        peer.sendall(self.frame(self.first, first))
        self.service.poll()
        original = self.receive(reply)
        self.assertEqual([(r['order'], r['shield'], r['hull']) for r in original],
                         [(1, 4, 3600), (2, 0, 3578)])
        mixed = [hit(2), hit(3, 943), hit(4)]
        peer.sendall(self.frame(self.first, mixed))
        self.service.poll()
        received = self.receive(reply)
        self.assertEqual(received[0], original[1])
        self.assertEqual([r['order'] for r in received], [2, 3, 4])
        self.assertEqual([call.args[1] for call in group.call_args_list],
                         [((1074, 26), (1074, 26)), ((943, 26), (1074, 26))])
        peer.sendall(self.frame(self.first, mixed))
        self.service.poll()
        self.assertEqual(self.receive(reply), received)
        peer.sendall(self.frame(self.first, [hit(2, 943), hit(3, 943)]))
        self.service.poll()
        self.assertEqual(reply.readline(), b'')
        self.assertEqual(group.call_count, 2)
        self.assertEqual(self.calls, [])

    def test_rejected_native_group_falls_back_to_its_reserved_orders(self):
        group = Mock(side_effect=CombatImpactRejected('all writes refused by native preflight'))
        single = Mock(side_effect=(CombatImpactRejected('target missing'), (4, 3600), (0, 3578)))
        self.grouped_service(single, group)
        first, first_reply = self.open()
        first.sendall(self.frame(self.first, [hit(1, 943), hit(2)]))
        self.service.poll()
        second, second_reply = self.open()
        second.sendall(self.frame(self.second, [hit(1)]))
        for _ in range(3):
            self.service.poll()
        self.assertEqual([(r['order'], r['status']) for r in self.receive(first_reply)],
                         [(1, 'rejected'), (2, 'confirmed')])
        self.assertEqual(self.receive(second_reply)[0]['order'], 3)
        self.assertFalse(self.service.poisoned)
        group.assert_called_once()
        self.assertEqual(single.call_count, 3)

    def test_group_settles_after_revocation_and_preserves_other_pilot_order(self):
        delivered = Mock(poll=Mock(return_value=None))
        group = Mock(return_value=delivered)
        self.grouped_service(self.apply, group)
        first, first_reply = self.open()
        first.sendall(self.frame(self.first, [hit(1), hit(2)]))
        self.service.poll()
        second, second_reply = self.open()
        second.sendall(self.frame(self.second, [hit(1)]))
        del self.grants[derive_combat_ticket(self.first['ticket'])]
        self.service.poll()
        self.assertEqual(self.calls, [])
        delivered.poll.return_value = ((4, 3600), (0, 3578))
        self.assertTrue(self.service.settle_pending())
        for _ in range(2):
            self.service.poll()
        self.assertEqual(first_reply.readline(), b'')
        self.assertEqual(self.receive(second_reply)[0]['order'], 3)
        group.assert_called_once()
        self.assertEqual(len(self.calls), 1)

    def test_incomplete_native_group_remains_unknown_and_refreshes_the_victim(self):
        group_operation = Mock(poll=Mock(return_value=None))
        group = Mock(return_value=group_operation)
        single = Mock()
        before = dict(shield=30, maxshield=2366, hull=3600, maxhull=3600)
        after = dict(before, shield=4)
        read = Mock(side_effect=(Mock(poll=Mock(return_value=before)), Mock(poll=Mock(return_value=after))))
        self.grouped_service(single, group, begin_health_read=read, health_window=lambda _: 3)
        victim = self.grant(1074)
        target_peer, target_reply = self.open()
        target_peer.sendall(self.health_request(victim, 3, 1))
        self.service.poll()
        initial = json.loads(target_reply.readline())
        self.assertEqual(initial['health'], before)

        peer, reply = self.open()
        frame = self.frame(self.first, [hit(1), hit(2, 943)])
        peer.sendall(frame)
        self.service.poll()
        peer.close()
        reply.close()
        retry, retry_reply = self.open()
        retry.sendall(frame)
        self.service.poll()
        self.assertTrue(self.service.native_pending)
        # A correct first row is not a complete receipt for the native frame.
        group_operation.poll.return_value = ((4, 3600),)
        with self.assertLogs('authority.company_combat_service', level='ERROR'):
            self.service.settle_pending()
        self.assertTrue(self.service.poisoned)
        for _ in range(2):
            self.service.poll()
        receipts = self.receive(retry_reply)
        self.assertEqual([(r['status'], r['shield'], r['hull']) for r in receipts],
                         [('unknown', None, None), ('unknown', None, None)])
        retry.sendall(frame)
        self.service.poll()
        self.assertEqual(self.receive(retry_reply), receipts)
        single.assert_not_called()
        group.assert_called_once()

        query = json.loads(self.health_request(victim, 3, 2))
        query['after_revision'] = initial['revision']
        target_peer.sendall(encode_frame(query))
        self.service.poll()
        self.assertEqual(json.loads(target_reply.readline())['health'], after)
        self.assertEqual(read.call_count, 2)

    def test_client_reconnects_only_with_an_unchanged_pending_batch(self):
        client = CompanyCombatClient(self.service.port, self.first)
        self.addCleanup(client.close)
        batch = [hit(1)]
        with self.assertRaises(TimeoutError):
            client.exchange(batch, timeout=0.02)
        with self.assertRaisesRegex(ValueError, 'unchanged'):
            client.exchange([hit(1, amount=25)])
        with ThreadPoolExecutor(max_workers=1) as worker:
            exchange = worker.submit(client.exchange, batch)
            deadline = time.monotonic() + 2
            while not exchange.done() and time.monotonic() < deadline:
                self.service.poll()
                try:
                    exchange.result(timeout=0.01)
                except FutureTimeout:
                    pass
            self.assertEqual(exchange.result(timeout=0)[0]['status'], 'confirmed')
        self.assertEqual(self.calls, [(1073, 1074, 26)])


if __name__ == '__main__':
    unittest.main()
