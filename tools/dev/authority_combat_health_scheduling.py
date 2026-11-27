"""Compare bounded health-read scheduling rules for CompanyCombatService offline.

Each rule subclasses the real service and only decides whether the next queued batch may be
dispatched on this owner tick. Batches stay FIFO, orders are still reserved at dispatch, and a
read is still cancelled before any native write begins, so native operation and receipt order are
unchanged. Only hit latency changes. The harness uses real loopback framing and a patched owner
clock, one service poll per 50 ms tick.

Run: python tools/dev/authority_combat_health_scheduling.py
"""
import json
import select
import socket
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
import authority.company_combat_service as S  # noqa: E402
from authority.company_combat_frames import encode_frame  # noqa: E402

TICK_S = 0.05
HEALTH = dict(shield=30, maxshield=100, hull=90, maxhull=100)
NEVER = None


class BoundedHold(S.CompanyCombatService):
    """Delay the head batch while a read started before it became ready is in flight.

    protect='all': any such read. protect='owed': only a seat whose last fresh read was preempted.
    protect='aged': only a seat without a confirmed fresh read for `age` seconds since its first
    request or last confirmation. The hold for one batch ends at `budget` seconds after the
    protected read started. Reads that start while the batch waits do not extend the hold.
    """

    def __init__(self, *args, budget, protect, age=None, backoff=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._budget, self._protect, self._age, self._backoff = budget, protect, age, backoff
        self._head = self._head_since = None
        self._owed = set()
        self._fresh_at = {}
        self._unprotected_until = {}
        self.held_ticks = 0

    def _needs_dispatch(self, batch):
        return batch.receipts is None or len(batch.receipts) < len(batch.impacts)

    def _hold(self, batch):
        now = S.time.monotonic()
        if self._head is not batch:
            self._head, self._head_since = batch, now
        for query in self._health_reads:
            if query.operation is None or query.started_at is None:
                continue
            seat = query.peer.binding[1:]
            if self._protect == 'owed' and seat not in self._owed:
                continue
            if self._protect == 'aged' and now - self._fresh_at.setdefault(seat, query.received_at) < self._age:
                continue
            if now < self._unprotected_until.get(seat, now):
                continue
            if query.started_at < self._head_since and now - query.started_at < self._budget:
                return True
        return False

    def _advance_batch(self):
        if self._batches and self._needs_dispatch(self._batches[0]) and self._hold(self._batches[0]):
            self.held_ticks += 1
            return
        super()._advance_batch()

    def _complete_health_read(self, query, health=None, *, outcome='unavailable'):
        seat = query.peer.binding[1:]
        self._fresh_at.setdefault(seat, query.received_at)
        super()._complete_health_read(query, health, outcome=outcome)
        if health is not None:
            self._owed.discard(seat)
            self._fresh_at[seat] = S.time.monotonic()
        elif outcome == 'preempted':
            self._owed.add(seat)
            now = S.time.monotonic()
            if self._backoff is not None and query.started_at is not None and now - query.started_at >= self._budget:
                # The protected read used its whole budget unanswered: stop protecting this seat for a while.
                self._unprotected_until[seat] = now + self._backoff


POLICIES = {
    'current': lambda **kw: S.CompanyCombatService(**kw),
    'owed_250ms': lambda **kw: BoundedHold(budget=0.25, protect='owed', **kw),
    'all_250ms': lambda **kw: BoundedHold(budget=0.25, protect='all', **kw),
    'all_500ms': lambda **kw: BoundedHold(budget=0.5, protect='all', **kw),
    'aged_2s_500ms': lambda **kw: BoundedHold(budget=0.5, protect='aged', age=2.0, **kw),
    'aged_2s_500ms_backoff5s': lambda **kw: BoundedHold(budget=0.5, protect='aged', age=2.0, backoff=5.0, **kw),
}


class _NativeRead:
    def __init__(self, latency):
        self.latency, self.polls = latency, 0

    def poll(self):
        self.polls += 1
        return HEALTH if self.latency is not NEVER and self.polls > self.latency else None

    def cancel(self):
        pass


class _NativeWrite:
    """Confirmed at the first settle poll after dispatch, like a one-frame X4 receipt."""

    def poll(self):
        return 0, 10**6


def _grant(grants, index):
    ticket = S.derive_combat_ticket(f'{index:064x}')
    generation = f'{index:032x}'
    grants[ticket] = (generation, 'b' * 32, index)
    return dict(ticket=ticket, generation=generation, registry_epoch='b' * 32, shooter_index=index)


def run(policy, *, readers=1, read_latency=2, shooters=2, ticks=200, wait=0.01):
    """Return hit and fresh-read delays in ms. read_latency is in ticks after the start poll, None = never."""
    grants = {}
    shooter_grants = [_grant(grants, index) for index in (1073, 1080, 1081, 1082)[:shooters]]
    reader_grants = [_grant(grants, index) for index in (943, 944, 945, 946)[:readers]]
    with patch('authority.company_combat_service.time') as clock:
        clock.monotonic.return_value = 100.0
        service = POLICIES[policy](
            lookup_current=grants.get,
            apply_impact=lambda binding, target, amount: _NativeWrite(),
            health_window=lambda binding: 3, max_health_reads=4,
            begin_health_read=lambda binding: _NativeRead(read_latency))
        links = []
        try:
            def connect():
                connection = socket.create_connection(('127.0.0.1', service.port), timeout=1)
                connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                links.append(connection)
                return connection, connection.makefile('rb')

            pilots = [dict(grant=g, link=connect(), next_id=1, sent_at=None) for g in shooter_grants]
            # The cockpit bridge asks at most once per second, staggered per pilot.
            watchers = [dict(grant=g, link=connect(), offset=n * 5, sequence=0, waiting=False,
                             asked_at=None, confirmed=[], requests=0) for n, g in enumerate(reader_grants)]
            hit_delays, orders = [], []
            for tick in range(ticks):
                clock.monotonic.return_value = 100.0 + tick * TICK_S
                for pilot in pilots:
                    if pilot['sent_at'] is None:
                        impact = dict(id=pilot['next_id'], target_index=1074, amount=26)
                        pilot['link'][0].sendall(encode_frame(dict(kind='combat_impacts', **pilot['grant'],
                                                                   impacts=[impact])))
                        pilot['next_id'] += 1
                        pilot['sent_at'] = tick
                for watcher in watchers:
                    if not watcher['waiting'] and (tick - watcher['offset']) % 20 == 0 and tick >= watcher['offset']:
                        watcher['sequence'] += 1
                        watcher['link'][0].sendall(encode_frame(dict(
                            kind='combat_health_read', **watcher['grant'], window=3,
                            sequence=watcher['sequence'], after_revision=None)))
                        watcher['waiting'], watcher['asked_at'] = True, tick
                        watcher['requests'] += 1
                service.poll()
                for pilot in pilots:
                    connection, stream = pilot['link']
                    if pilot['sent_at'] is not None and select.select([connection], [], [], wait)[0]:
                        orders.extend(r['order'] for r in json.loads(stream.readline())['receipts'])
                        hit_delays.append((tick - pilot['sent_at']) * TICK_S)
                        pilot['sent_at'] = None
                for watcher in watchers:
                    connection, stream = watcher['link']
                    if watcher['waiting'] and select.select([connection], [], [], wait)[0]:
                        reply = json.loads(stream.readline())
                        if reply['health'] is not None:
                            watcher['confirmed'].append((watcher['asked_at'], tick))
                        watcher['waiting'] = False
            stats = service.health_read_stats()
        finally:
            for connection in links:
                connection.close()
            service.close()

    def stale(watcher):
        # Longest time this pilot went without a confirmed fresh read, from its first request.
        marks = [watcher['offset']] + [done for _, done in watcher['confirmed']] + [ticks]
        return max(b - a for a, b in zip(marks, marks[1:])) * TICK_S

    ms = lambda seconds: round(seconds * 1000)  # noqa: E731
    return dict(
        policy=policy, readers=readers, read_latency=read_latency,
        hits=len(hit_delays), max_hit_ms=ms(max(hit_delays)),
        fresh=sum(len(w['confirmed']) for w in watchers), requests=sum(w['requests'] for w in watchers),
        max_read_ms=ms(max((done - asked for w in watchers for asked, done in w['confirmed']), default=0) * TICK_S)
        if any(w['confirmed'] for w in watchers) else None,
        max_stale_ms=ms(max(stale(w) for w in watchers)),
        held_ticks=getattr(service, 'held_ticks', 0), preempted=stats['preempted'],
        orders_increasing=orders == sorted(set(orders)))


SCENARIOS = [(1, 2), (1, 8), (1, NEVER), (4, 2), (4, 8), (4, NEVER)]


def main():
    rows = [run(policy, readers=readers, read_latency=latency)
            for readers, latency in SCENARIOS for policy in POLICIES]
    print('| policy | readers | read latency | hits/10 s | max hit ms | fresh/asked | max read ms | max stale ms | held ticks |')
    print('|---|---|---|---|---|---|---|---|---|')
    for row in rows:
        latency = 'never' if row['read_latency'] is NEVER else f"{row['read_latency'] * 50} ms"
        print(f"| {row['policy']} | {row['readers']} | {latency} | {row['hits']} | {row['max_hit_ms']} | "
              f"{row['fresh']}/{row['requests']} | {row['max_read_ms']} | {row['max_stale_ms']} | {row['held_ticks']} |")
    return rows


if __name__ == '__main__':
    main()
