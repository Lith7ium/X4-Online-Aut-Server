"""One admission-bound snapshot between the combat worker and owned X4 UDP."""
import re
import threading
from copy import deepcopy
from math import isclose

from .company_motion_frames import MAX_COUNTER
from .company_npc_measurement import native_capture_now

_FIELDS = ('shield', 'maxshield', 'hull', 'maxhull')
_TOKEN = re.compile(r'[0-9a-f]{32}')
_LIFETIME = 2.0
_SAMPLE_AGE = 1.0
_INTERVAL = 1.0
_MAX_WINDOW = 2**31 - 1


def _safe_health(state):
    return (type(state) is dict and state.keys() == {*_FIELDS, 'revision'}
            and type(state['revision']) is int and 0 <= state['revision'] <= MAX_COUNTER
            and all(type(state[key]) in (int, float) and 0 <= state[key] <= MAX_COUNTER for key in _FIELDS)
            and state['shield'] <= state['maxshield'] and 0 < state['hull'] <= state['maxhull'])


def _matches_readback(server, cockpit):
    if (server['maxshield'], server['maxhull']) != (cockpit['maxshield'], cockpit['maxhull']):
        return False
    for key in ('shield', 'hull'):
        # The native setters convert an absolute value to a percentage. A zero shield stays exactly zero.
        tolerance = 0 if server[key] == 0 else max(.0001, server[key] * 2e-7)
        if not isclose(cockpit[key], server[key], rel_tol=0, abs_tol=tolerance):
            return False
    return True


class CompanyCockpitHealthBridge:
    """The motion owner stages fresh requests and delivers responses to its X4 peer."""

    def __init__(self, binding, *, clock=native_capture_now):
        self.generation, self.epoch, self.index = binding
        if (not all(type(token) is str and _TOKEN.fullmatch(token) for token in binding[:2])
                or type(self.index) is not int or not 1 <= self.index <= 200000):
            raise ValueError('invalid cockpit health grant')
        self._clock = clock
        self._lock = threading.Lock()
        self._window = self._sample = self._pending = self._ready = self._sent = None
        self._after_revision = self._last_dispatched = None
        self._last_confirmed = None
        self._revision = self._sequence = 0
        self._next_request = self._active_since = 0.0
        self._clear = self._closed = False
        self._counts = dict(requested=0, delivered=0, confirmed=0, rejected=0, unknown=0, expired=0)

    def _invalidate(self):
        self._sample = self._pending = self._ready = self._sent = None
        self._after_revision = None
        self._revision += 1
        self._clear = True
        self._next_request = self._active_since = self._clock()

    def on_clock(self, window, running):
        current = window if running and type(window) is int and 1 <= window <= _MAX_WINDOW else None
        with self._lock:
            if not self._closed and current != self._window:
                self._window = current
                self._invalidate()

    def on_gap(self):
        with self._lock:
            if self._sample is not None:
                self._invalidate()

    def on_sample(self, sample):
        with self._lock:
            now = self._clock()
            self._expire(now)
            if self._window is not None and self._active_since <= sample['capture_time'] <= now:
                self._sample = sample['sequence'], sample['capture_time']

    def _expire(self, now):
        if self._sample is not None and now - self._sample[1] > _SAMPLE_AGE:
            self._invalidate()
        elif self._pending is not None and now >= self._pending[2]:
            self._pending = self._ready = self._sent = None
            self._counts['expired'] += 1

    def take_request(self):
        """Called by the combat worker. Take at most one live request per second."""
        with self._lock:
            now = self._clock()
            self._expire(now)
            if (self._sample is None or self._pending is not None or now < self._next_request
                    or self._sequence >= MAX_COUNTER):
                return None
            self._sequence += 1
            self._next_request = now + _INTERVAL
            self._pending = self._window, self._sequence, now + _LIFETIME, self._sample[0]
            self._counts['requested'] += 1
            return self._window, self._sequence, self._after_revision

    def accept_reply(self, window, sequence, state):
        """Called by the combat worker after its authenticated request returns."""
        with self._lock:
            self._expire(self._clock())
            if (self._pending is None or self._pending[:2] != (window, sequence)
                    or self._ready is not None or self._sent is not None):
                return False
            if (state is None or not _safe_health(state)
                    or (self._after_revision is not None and state['revision'] < self._after_revision)):
                self._pending = None
                return False
            self._ready = state['revision'], tuple(state[key] for key in _FIELDS)
            return True

    def deliver(self, send):
        """Called on the motion owner thread, after draining the native UDP input."""
        with self._lock:
            self._expire(self._clock())
            clear = self._revision if self._clear else None
            pending = self._pending
            ready = self._ready
        if clear is not None:
            if not send(f'HEALTH_CLEAR|{clear}'):
                return
            with self._lock:
                if self._revision == clear:
                    self._clear = False
        if ready is None:
            return
        with self._lock:
            self._expire(self._clock())
            if self._pending != pending or self._ready is not ready or self._clear:
                return
            window, sequence, deadline, sample = pending
            revision, values = ready
            fields = '|'.join(format(value, '.17g') for value in (deadline, *values))
            body = (f'HEALTH|{self._revision}|{self.generation}|{self.epoch}|{self.index}|'
                    f'{window}|{sequence}|{sample}|{fields}')
            # Keep the send one-shot. A fresh read is needed after a lost native receipt.
            self._ready, self._sent = None, (window, sequence, revision, values)
        if send(body):
            with self._lock:
                self._last_dispatched = str(window), str(sequence)
                self._counts['delivered'] += 1

    def native_result(self, fields):
        """Consume a receipt from the unchanged, verified owned X4 UDP peer."""
        if len(fields) not in (7, 11):
            return
        _, generation, epoch, index, window, sequence, status = fields[:7]
        if ((generation, epoch, index) != (self.generation, self.epoch, str(self.index))
                or status not in ('confirmed', 'rejected', 'unknown') or (status == 'confirmed') != (len(fields) == 11)):
            return
        with self._lock:
            if status == 'unknown' and not self._closed and (window, sequence) == self._last_dispatched:
                self._counts['unknown'] += 1
                self._window = None
                self._closed = True
                self._invalidate()
                return
            self._expire(self._clock())
            if self._sent is not None and (window, sequence) == tuple(map(str, self._sent[:2])):
                if status == 'confirmed':
                    _, _, revision, expected_values = self._sent
                    server = dict(zip(_FIELDS, expected_values))
                    try:
                        cockpit = dict(zip(_FIELDS, map(float, fields[7:])))
                    except ValueError:
                        return
                    if not _safe_health(dict(cockpit, revision=revision)) or not _matches_readback(server, cockpit):
                        return
                    self._after_revision = revision
                    self._last_confirmed = dict(
                        window=int(window), sequence=int(sequence), revision=revision,
                        server=server, cockpit=cockpit)
                self._pending = self._sent = None
                self._counts[status] += 1

    def stats(self):
        with self._lock:
            result = dict(self._counts)
            if self._last_confirmed is not None:
                result['last_confirmed_readback'] = deepcopy(self._last_confirmed)
            return result

    def close(self):
        with self._lock:
            self._closed = True
            self._window = None
            self._invalidate()
