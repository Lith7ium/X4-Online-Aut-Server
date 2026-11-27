"""A loopback TCP relay that delays and stalls bytes like a poor internet link.

The link model matches tools/dev/authority_flight_lab.lua draw for draw, so a
seed gives the same arrival times here and in the Lua lab. Lost packets are
resent after a retransmission timeout and every later byte waits behind them,
as TCP does. Use it only on 127.0.0.1 in tests and the flight lab.
"""
from collections import deque
import math
import socket
import threading
import time

PROFILES = {
    'loopback': {'delay': 0.0, 'jitter': 0.0, 'loss': 0.0, 'burst': 1},
    'good': {'delay': .030, 'jitter': .005, 'loss': 0.0, 'burst': 1},
    'home': {'delay': .060, 'jitter': .020, 'loss': .005, 'burst': 1},
    'poor': {'delay': .120, 'jitter': .040, 'loss': .02, 'burst': 3},
}


class _ParkMiller:
    def __init__(self, seed: int):
        if type(seed) is not int or not 1 <= seed < 2147483647:
            raise ValueError('seed must be an integer from 1 to 2147483646')
        self._state = seed

    def __call__(self) -> float:
        self._state = self._state * 16807 % 2147483647
        return self._state / 2147483647


class LinkModel:
    """Arrival times for packets sent in order over one direction of a link."""

    def __init__(self, profile: str, seed: int):
        if profile not in PROFILES:
            raise ValueError('unknown link profile')
        self._p = PROFILES[profile]
        self._draw = _ParkMiller(seed)
        self._previous = -math.inf
        self._last_departure = -math.inf
        self._burst_left = 0

    def arrival(self, departure: float) -> float:
        if departure < self._last_departure:
            raise ValueError('departures must not go back')
        self._last_departure = departure
        p = self._p
        u1, u2 = self._draw(), self._draw()
        own = departure + p['delay'] + p['jitter'] * (-math.log(1 - u1))
        lost = self._burst_left > 0
        if lost:
            self._burst_left -= 1
        elif u2 < p['loss']:
            lost, self._burst_left = True, p['burst'] - 1
        if lost:
            wait = .2 + 2 * p['delay']
            total = wait
            while self._draw() < p['loss']:
                wait *= 2
                total += wait
            own += total
        self._previous = max(self._previous, own)
        return self._previous


def _leg(ready, forward, back):
    """Send the newest ready sample, then wait for its acknowledgement, as the movement client and service do."""
    out, free, j = [], -math.inf, 0
    while j < len(ready):
        send_at = max(free, ready[j][1])
        while j + 1 < len(ready) and ready[j + 1][1] <= send_at:
            j += 1
        arrive = forward.arrival(send_at)
        out.append((ready[j][0], arrive))
        free = back.arrival(arrive)
        j += 1
    return out


def path_model(profile: str, seed: int, departures: list[float]) -> list[tuple[int, float]]:
    """Pilot to server to observer, as L.path in the Lua lab: [(1-based k, arrival)] for delivered samples."""
    ready = [(k, t) for k, t in enumerate(departures, start=1)]
    to_server = _leg(ready, LinkModel(profile, seed), LinkModel(profile, seed + 1))
    return _leg(to_server, LinkModel(profile, seed + 2), LinkModel(profile, seed + 3))


class ImpairedRelay:
    """Forwards one or more loopback connections to target_port through the link model.

    Direction 0 carries client bytes to the target, direction 1 the replies.
    log holds (direction, received_at, released_at, nbytes) for every chunk,
    with released_at the scheduled release time on time.monotonic().
    """

    def __init__(self, target_port: int, profile: str, seed: int):
        if profile not in PROFILES:
            raise ValueError('unknown link profile')
        self._target_port, self._profile, self._seed = target_port, profile, seed
        self._listener = socket.create_server(('127.0.0.1', 0))
        self.port = self._listener.getsockname()[1]
        self.log = []
        self._log_lock = threading.Lock()
        self._sockets = [self._listener]
        self._closed = threading.Event()
        self._start = time.monotonic()
        self._threads = [threading.Thread(target=self._accept, daemon=True)]
        self._threads[0].start()

    def _accept(self):
        while not self._closed.is_set():
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            try:
                server = socket.create_connection(('127.0.0.1', self._target_port))
            except OSError:
                client.close()
                continue
            for sock in (client, server):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._sockets += [client, server]
            for direction, (source, destination) in enumerate(((client, server), (server, client))):
                self._direction(direction, source, destination)

    def _direction(self, direction, source, destination):
        model = LinkModel(self._profile, self._seed + direction)
        queue, ready = deque(), threading.Condition()

        def read():
            while True:
                try:
                    data = source.recv(65536)
                except OSError:
                    data = b''
                received = time.monotonic()
                with ready:
                    if data:
                        release = self._start + model.arrival(received - self._start)
                        queue.append((release, received, data))
                    else:
                        queue.append(None)
                    ready.notify()
                if not data:
                    return

        def write():
            while True:
                with ready:
                    while not queue:
                        ready.wait()
                    item = queue.popleft()
                if item is None:
                    try:
                        destination.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    return
                release, received, data = item
                pause = release - time.monotonic()
                if pause > 0:
                    time.sleep(pause)
                try:
                    destination.sendall(data)
                except OSError:
                    return
                with self._log_lock:
                    self.log.append((direction, received, release, len(data)))

        for target in (read, write):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self._threads.append(thread)

    def close(self):
        self._closed.set()
        for sock in self._sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        for thread in self._threads:
            thread.join(2)


def _model_delay(relay, direction, after):
    with relay._log_lock:
        log = list(relay.log)
    for d, received, released, _ in log:
        if d == direction and received >= after:
            return released - received, released
    return None, None


def measure_motion_transport(profile='home', *, count=100, interval=.1, seed=11, settle=10.0):
    """Send count samples from one real movement client to another through two relays.

    Uses the real CompanyMotionService, CompanyMotionClient and cockpit observer
    on 127.0.0.1, with no X4. Returns when each sample was queued and when its
    cockpit message reached the observer, plus each side's client statistics.
    """
    from authority.company_motion_client import CompanyMotionClient
    from authority.company_motion_service import CompanyMotionService
    from authority.company_motion_source import CompanyMotionSource

    epoch, session, sector = 'a' * 32, 'b' * 32, 'cluster_06_sector002_macro'

    class _Queue:
        def __init__(self, index, generation):
            self.registry = (epoch, index)
            self.local_generation = generation
            self.items = []

        def poll(self, _):
            return self.items.pop(0) if self.items else None

    def sample(index, generation, k, x, speed):
        return {'version': 3, 'epoch': epoch, 'generation': generation, 'index': index, 'sequence': k,
                'sector_macro': sector, 'source_time': 10.0 + k * interval, 'capture_time': 20.0 + k * interval,
                'x': x, 'y': 0.0, 'z': 0.0, 'real_velocity': {'x': speed, 'y': 0.0, 'z': 0.0}}

    service = CompanyMotionService()
    relays, clients, source = [], [], None
    queued, seen = {}, {}
    try:
        g_recv = service.admit(1, '1' * 32, 1073, sector, session, epoch)
        g_send = service.admit(2, '2' * 32, 1074, sector, session, epoch)
        service.set_running(True)
        source = CompanyMotionSource(playback_index=1074, cockpit_radar_playback=True)
        source.configure(epoch, 1073, operational_bits='1' * 3000)
        observer = source.observe(lambda _: True)

        def to_x4(message):
            if message.startswith(f'REMOTE|cockpit|1|{epoch}|1074|'):
                seen.setdefault(int(message.split('|')[6]), time.monotonic())
            return True

        observer._send = to_x4
        relay_send = ImpairedRelay(service.port, profile, seed + 10)
        relay_recv = ImpairedRelay(service.port, profile, seed)
        relays += [relay_send, relay_recv]
        # The observing pilot publishes its own position too, as a real pilot does.
        own = _Queue(1073, 'f' * 32)
        receiver = CompanyMotionClient(
            relay_recv.port, g_recv, own, lambda: True,
            on_snapshot=observer.snapshot, on_end=observer.end, on_transport_lost=observer.transport_lost,
            on_view_reset=observer.reset_world, trial_index=1074)
        observer.bind_views(receiver.set_views)
        pilot = _Queue(1074, 'c' * 32)
        sender = CompanyMotionClient(relay_send.port, g_send, pilot, lambda: True)
        clients += [receiver, sender]

        view = ['VIEW', 'cockpit', '1', epoch, sector, '1074', '0x200', 'KHI-630']
        viewed = [None]

        def pump(until, timeout):
            deadline = time.monotonic() + timeout
            while not until() and time.monotonic() < deadline:
                # X4's cockpit script renews the same view well inside its 3 s lifetime.
                if viewed[0] is not None and time.monotonic() - viewed[0] >= 1.0:
                    observer.receive(view)
                    viewed[0] = time.monotonic()
                service.poll()
                for client in clients:
                    client.poll()
                time.sleep(.002)
            return until()

        if not pump(lambda: receiver._running and sender._running, 10):
            raise TimeoutError('movement clients did not start through the relays')
        observer.receive(view)
        viewed[0] = time.monotonic()
        if not pump(lambda: receiver._sent_view == {sector: frozenset({1074})}, 10):
            raise TimeoutError('cockpit view did not reach the service')
        start = time.monotonic()
        for k in range(1, count + 1):
            due = start + (k - 1) * interval
            pump(lambda: time.monotonic() >= due, interval * 2 + 1)
            queued[k] = time.monotonic()
            pilot.items.append(sample(1074, 'c' * 32, k, 1000.0 + 10 * k, 10 / interval))
            own.items.append(sample(1073, 'f' * 32, k, -5000.0, 0.0))
        pump(lambda: count in seen, settle)
        extra = []
        for k in sorted(seen):
            d1, released = _model_delay(relay_send, 0, queued[k])
            d2, _ = _model_delay(relay_recv, 1, released) if released is not None else (None, None)
            if d1 is not None and d2 is not None:
                extra.append(seen[k] - queued[k] - d1 - d2)
        return {'profile': profile, 'queued': queued, 'seen': seen, 'extra': extra,
                'sender': sender.stats(), 'receiver': receiver.stats()}
    finally:
        for client in clients:
            client.close()
        if source is not None:
            source.close()
        for relay in relays:
            relay.close()
        service.close()
