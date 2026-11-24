"""Correlate local native view claims with deliveries from the admitted motion stream."""
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
import re
import time

from .company_motion_frames import MAX_COUNTER, SECTOR
from .experimental_tcp_motion import motion_value

_TOKEN = re.compile(r'[0-9a-f]{32}')
_COUNTER = re.compile(r'[1-9][0-9]{0,15}')
_ACTOR = re.compile(r'(?:0[xX][0-9a-fA-F]{1,16}|[1-9][0-9]{0,19})')
_CODE = re.compile(r'[A-Z0-9]{3}-[A-Z0-9]{3}')
_TTL = 3.0
_MAX_PENDING = 4
_SLOTS = ('cockpit', 'map')
# A full SEEN can reach 543 bytes with the lease/generation prefix,
# a 128-byte sector macro and eight round-trippable doubles.
MAX_OBSERVER_DATAGRAM = 576


def _counter(text, limit=MAX_COUNTER):
    if _COUNTER.fullmatch(text):
        number = int(text)
        if number <= limit:
            return number
    return None


def _valid_actor(actor):
    if not _ACTOR.fullmatch(actor):
        return False
    base = 16 if actor[:2].lower() == '0x' else 10
    return 0 < int(actor, base) < 2**64


@dataclass
class _View:
    serial: int = 0
    binding: tuple | None = None
    active: tuple | None = None
    deadline: float = 0
    pending: deque = field(default_factory=lambda: deque(maxlen=_MAX_PENDING))
    samples_confirmed: int = 0
    velocity_samples_confirmed: int = 0
    last_confirmed: dict | None = None
    last_nonzero_velocity_confirmed: dict | None = None

    def replace(self, binding):
        self.active = binding
        self.pending.clear()


class CompanyMotionObserver:
    """Keep one observed hull per native slot and a bounded receipt history."""

    def __init__(self, registry, send, *, trial_index=None):
        self._epoch, self._own_index = registry
        if trial_index is not None and (type(trial_index) is not int or not 1 <= trial_index <= 200000
                                        or trial_index == self._own_index):
            raise ValueError('invalid local cockpit trial index')
        self._trial_index = trial_index
        self._trial = None
        self._send = send
        self._set_views = None
        self._slots = {name: _View() for name in _SLOTS}
        self._subscribed = ()
        self._last_confirmed = None

    def bind_views(self, set_views):
        self._set_views = set_views

    def _sync_views(self):
        sectors = {}
        for view in self._slots.values():
            if view.active is not None:
                sector, index, _, _ = view.active
                sectors.setdefault(sector, set()).add(index)
        subscribed = tuple((sector, tuple(sorted(indices))) for sector, indices in sorted(sectors.items()))
        if subscribed != self._subscribed:
            self._subscribed = subscribed
            self._set_views([{'sector_macro': sector, 'indices': list(indices)} for sector, indices in subscribed])

    def _end_trial(self):
        if self._trial is None:
            return
        serial, index, generation, sector = self._trial
        self._trial = None
        # A still-queued native SEEN must not certify a pose after revocation.
        self._slots['cockpit'].pending.clear()
        self._send(f'END|cockpit|{serial}|{self._epoch}|{index}|{generation}|{sector}')

    def expire(self):
        now = time.monotonic()
        for slot, view in self._slots.items():
            if view.active is not None and now >= view.deadline:
                if slot == 'cockpit':
                    self._end_trial()
                view.replace(None)
        self._sync_views()

    def _view(self, view, serial, sector, index_text, actor, code):
        if (sector, index_text, actor, code) == ('-', '0', '-', '-'):
            binding = None
        else:
            index = _counter(index_text, 200000)
            if (index is None or index == self._own_index or not SECTOR.fullmatch(sector)
                    or not _valid_actor(actor) or not _CODE.fullmatch(code)):
                return
            binding = (sector, index, actor, code)
        if serial < view.serial or (serial == view.serial and binding != view.binding):
            return
        new_serial = serial != view.serial
        view.serial, view.binding = serial, binding
        view.deadline = time.monotonic() + _TTL
        if new_serial or binding != view.active:
            if view is self._slots['cockpit']:
                self._end_trial()
            view.replace(binding)
            self._sync_views()

    def receive(self, fields):
        """Receive only from the source's authenticated, process-owned peer."""
        if self._set_views is None or len(fields) not in (8, 18):
            return
        kind, slot, serial_text, epoch, *body = fields
        view = self._slots.get(slot)
        serial = _counter(serial_text)
        if view is None or serial is None or epoch != self._epoch:
            return
        self.expire()
        if kind == 'VIEW' and len(body) == 4:
            self._view(view, serial, *body)
        elif kind == 'SEEN' and len(body) == 14 and serial == view.serial and view.active is not None:
            self._seen(slot, view, *body[:6], tuple(body[6:]))

    def _seen(self, slot, view, index_text, generation, sequence_text, sector, actor, code, values):
        index, sequence = _counter(index_text, 200000), _counter(sequence_text)
        if ((sector, index, actor, code) != view.active or sequence is None
                or not _TOKEN.fullmatch(generation)):
            return
        now = time.monotonic()
        for offset, (sample, expected_values, sent_at) in enumerate(view.pending):
            if generation != sample['generation'] or sequence != sample['sequence']:
                continue
            if values != expected_values:
                return
            for _ in range(offset + 1):
                view.pending.popleft()
            if now - sent_at >= _TTL:
                return
            velocity = sample['real_velocity']
            view.samples_confirmed += 1
            view.velocity_samples_confirmed += int(velocity is not None)
            view.last_confirmed = self._last_confirmed = {
                'slot': slot, 'serial': view.serial, 'epoch': self._epoch, 'index': index,
                'generation': generation, 'sequence': sequence, 'sector_macro': sector,
                'code': code, 'position': {axis: sample[axis] for axis in 'xyz'},
                'real_velocity': velocity, 'source_time': sample['source_time'],
                'capture_time': sample['capture_time'],
            }
            if velocity is not None and any(component != 0 for component in velocity.values()):
                view.last_nonzero_velocity_confirmed = view.last_confirmed
            return

    def snapshot(self, sample):
        self.expire()
        sample = motion_value(sample)
        if sample['version'] != 3 or sample['epoch'] != self._epoch:
            return
        rate = sample['real_velocity']
        position = tuple(format(sample[axis], '.17g') for axis in 'xyz')
        velocity = ('-', '-', '-') if rate is None else tuple(format(rate[axis], '.17g') for axis in 'xyz')
        clocks = tuple(format(sample[key], '.17g') for key in ('source_time', 'capture_time'))
        values = (*position, *velocity, *clocks)
        for slot, view in self._slots.items():
            if view.active is None or (sample['sector_macro'], sample['index']) != view.active[:2]:
                continue
            trial = None
            if slot == 'cockpit' and sample['index'] == self._trial_index:
                trial = view.serial, sample['index'], sample['generation'], sample['sector_macro']
                if self._trial != trial:
                    self._end_trial()
            message = '|'.join(map(str, (
                'REMOTE', slot, view.serial, self._epoch, sample['index'], sample['generation'],
                sample['sequence'], sample['sector_macro'], *values,
            )))
            if self._send(message):
                view.pending.append((sample, values, time.monotonic()))
                if trial is not None:
                    self._trial = trial

    def end(self, index, generation):
        if self._trial is not None and self._trial[1:3] == (index, generation):
            self._end_trial()
        for view in self._slots.values():
            if view.active is not None and view.active[1] == index:
                view.pending = deque((item for item in view.pending if item[0]['generation'] != generation),
                                     maxlen=_MAX_PENDING)

    def stats(self):
        slots = {
            name: {'samples_confirmed': slot.samples_confirmed,
                   'velocity_samples_confirmed': slot.velocity_samples_confirmed,
                   'last_confirmed': slot.last_confirmed,
                   'last_nonzero_velocity_confirmed': slot.last_nonzero_velocity_confirmed}
            for name, slot in self._slots.items()
        }
        return deepcopy({
            'samples_confirmed': sum(slot.samples_confirmed for slot in self._slots.values()),
            'velocity_samples_confirmed': sum(slot.velocity_samples_confirmed for slot in self._slots.values()),
            'last_confirmed': self._last_confirmed,
            'slots': slots,
        })
