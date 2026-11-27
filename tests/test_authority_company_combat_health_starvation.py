"""Deterministic offline reproducer: sustained hits versus another pilot's fresh cockpit reads.

Real loopback framing with a simulated owner clock, one service poll per 50 ms owner tick.
Each dispatched impact first cancels every in-flight read (CompanyCombatService._dispatch_remaining),
and no read starts while a native write is pending. The cockpit bridge asks for at most one
read per second, and a fresh read stays due until one is confirmed.
"""
import json
import select
import socket
import unittest
from unittest.mock import patch

from authority.company_combat_frames import encode_frame
from authority.company_combat_service import CompanyCombatService, derive_combat_ticket

TICK_S = 0.05
TICKS = 200  # 10 s
READ_EVERY = 20  # the cockpit bridge's one-second request interval
HEALTH = dict(shield=30, maxshield=100, hull=90, maxhull=100)


class _NativeRead:
    """A stock cockpit read that answers `latency` polls after the poll that started it."""

    def __init__(self, latency, log, tick):
        self.latency, self.log, self.started, self.polls = latency, log, tick, 0

    def poll(self):
        self.polls += 1
        return HEALTH if self.polls > self.latency else None

    def cancel(self):
        # The service also releases an answered read. Record only reads cut off before their answer.
        if self.polls <= self.latency:
            self.log.append(self.started)


class _NativeWrite:
    """A stock damage write confirmed at the first settle poll after dispatch."""

    def poll(self):
        return 0, 10**6


def _grant(grants, index):
    ticket = derive_combat_ticket(f'{index:064x}')
    generation = f'{index:032x}'
    grants[ticket] = (generation, 'b' * 32, index)
    return dict(ticket=ticket, generation=generation, registry_epoch='b' * 32, shooter_index=index)


class HealthReadStarvationTests(unittest.TestCase):
    def run_ticks(self, *, shooters, hit_every, read_latency, async_write):
        clock = self.enterContext(patch('authority.company_combat_service.time'))
        clock.monotonic.return_value = 100.0
        grants, cancelled, now = {}, [], [0]
        reader_grant = _grant(grants, 943)
        shooter_grants = [_grant(grants, index) for index in (1073, 1080)[:shooters]]
        service = CompanyCombatService(
            grants.get, lambda binding, target, amount: _NativeWrite() if async_write else (0, 10**6),
            health_window=lambda binding: 3, max_health_reads=4,
            begin_health_read=lambda binding: _NativeRead(read_latency, cancelled, now[0]))
        self.addCleanup(service.close)

        def connect():
            connection = socket.create_connection(('127.0.0.1', service.port), timeout=1)
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            stream = connection.makefile('rb')
            self.addCleanup(connection.close)
            self.addCleanup(stream.close)
            return connection, stream

        reader, reader_in = connect()
        pilots = [dict(grant=grant, link=connect(), next_id=1, outstanding=False) for grant in shooter_grants]
        receipts = hits = 0
        replies = []
        for tick in range(TICKS):
            now[0] = tick
            clock.monotonic.return_value = 100.0 + tick * TICK_S
            for pilot in pilots:
                if not pilot['outstanding'] and tick % hit_every == 0:
                    impact = dict(id=pilot['next_id'], target_index=1074, amount=26)
                    pilot['link'][0].sendall(encode_frame(dict(kind='combat_impacts', **pilot['grant'],
                                                               impacts=[impact])))
                    pilot['next_id'] += 1
                    pilot['outstanding'] = True
                    hits += 1
            if tick % READ_EVERY == 0:
                reader.sendall(encode_frame(dict(kind='combat_health_read', **reader_grant, window=3,
                                                 sequence=tick // READ_EVERY + 1, after_revision=None)))
            service.poll()
            for pilot in pilots:
                connection, stream = pilot['link']
                if pilot['outstanding'] and select.select([connection], [], [], 0.02)[0]:
                    receipts += len(json.loads(stream.readline())['receipts'])
                    pilot['outstanding'] = False
            while select.select([reader], [], [], 0.02)[0]:
                replies.append((tick, json.loads(reader_in.readline())['health']))
        return dict(stats=service.health_read_stats(), receipts=receipts, hits=hits, replies=replies,
                    cancelled=cancelled)

    def windows(self, result):
        """Ticks from a read's start to its cancelling dispatch, one entry per cancelled read."""
        cancelled = [tick for tick, health in result['replies'] if health is None]
        return [tick - start for tick, start in zip(cancelled, result['cancelled'], strict=True)]

    def assert_starved(self, result):
        stats = result['stats']
        # Every hit on the registered ship is confirmed, but none of the other pilot's
        # ten fresh reads in 10 s returns health.
        self.assertGreaterEqual(result['receipts'], result['hits'] - 2)
        self.assertEqual(len(result['replies']), 10)
        self.assertTrue(all(health is None for _, health in result['replies']))
        self.assertEqual((stats['confirmed'], stats['preempted'], stats['native_started']), (0, 10, 10))

    def test_synchronous_hits_every_tick_cancel_a_one_tick_read(self):
        result = self.run_ticks(shooters=1, hit_every=1, read_latency=1, async_write=False)
        self.assert_starved(result)
        self.assertEqual(result['hits'], 200)
        self.assertEqual(self.windows(result), [1] * 10)

    def test_synchronous_hits_with_one_idle_tick_let_the_read_finish(self):
        result = self.run_ticks(shooters=1, hit_every=2, read_latency=1, async_write=False)
        self.assertEqual((result['stats']['confirmed'], result['stats']['preempted']), (10, 0))
        self.assertTrue(all(health == HEALTH for _, health in result['replies']))
        self.assertEqual(result['stats']['max_confirmed_native_ms'], 50.0)

    def test_back_to_back_native_writes_from_two_shooters_cancel_a_two_tick_read(self):
        # The X4 path: a write settles one tick after dispatch and its receipts leave one tick
        # later. A read can start only in the settle or receipt tick. A queued batch is
        # dispatched on the next tick and cancels the read first.
        result = self.run_ticks(shooters=2, hit_every=1, read_latency=2, async_write=True)
        self.assert_starved(result)
        self.assertEqual(sorted(self.windows(result)), [1] * 3 + [2] * 7)

    def test_back_to_back_native_writes_cancel_a_one_tick_read_that_starts_on_the_receipt_tick(self):
        result = self.run_ticks(shooters=2, hit_every=1, read_latency=1, async_write=True)
        self.assertEqual((result['stats']['confirmed'], result['stats']['preempted']), (7, 3))
        self.assertEqual(self.windows(result), [1] * 3)


if __name__ == '__main__':
    unittest.main()
