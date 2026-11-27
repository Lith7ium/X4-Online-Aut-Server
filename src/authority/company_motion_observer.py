"""Correlate local native view claims with deliveries from the admitted motion stream."""
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
import re
import sys
import time

from .company_limits import MAX_COCKPIT_PLAYBACK_PILOTS as MAX_COCKPIT_PILOTS
from .company_motion_frames import MAX_COUNTER, SECTOR
from .experimental_tcp_motion import ORIENTATION_AXES, REAL_TIME_MOTION_VERSIONS, motion_value

_TOKEN = re.compile(r'[0-9a-f]{32}')
_COUNTER = re.compile(r'[1-9][0-9]{0,15}')
_ACTOR = re.compile(r'(?:0[xX][0-9a-fA-F]{1,16}|[1-9][0-9]{0,19})')
_CODE = re.compile(r'[A-Z0-9]{3}-[A-Z0-9]{3}')
_TTL = 3.0
_ROSTER_HEARTBEAT = 0.5
_MAX_PENDING = 4
_SLOTS = ('cockpit', 'map')
COCKPIT_SLOTS = ('cockpit', 'cockpit2', 'cockpit3')
# A full SEEN can reach 544 bytes with the lease/generation prefix,
# cockpit2/cockpit3 slot name, a 128-byte sector macro and eight round-trippable doubles.
MAX_OBSERVER_DATAGRAM = 576
# SEEN4 adds three round-trippable angles.
MAX_ORIENTED_OBSERVER_DATAGRAM = 640
ROSTER_SAMPLE_SUFFIX = '|@roster|'
# A revision has at most 16 digits and each of three indices uses up to 7
# bytes including the separator. The native UDP module allows 768 bytes.
MAX_ROSTER_SUFFIX_BYTES = len(ROSTER_SAMPLE_SUFFIX) + 16 + MAX_COCKPIT_PILOTS * 7


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
    """Keep one observed hull per native slot (up to 3 cockpit slots plus independent map) and a bounded receipt history."""

    def __init__(self, registry, send, *, trial_index=None, cockpit_playback_slots=0, private_map_marker=False,
                 roster_with_samples=False):
        self._epoch, self._own_index = registry
        if trial_index is not None and (type(trial_index) is not int or not 1 <= trial_index <= 200000
                                        or trial_index == self._own_index):
            raise ValueError('invalid local cockpit trial index')
        if type(cockpit_playback_slots) is not int or not 0 <= cockpit_playback_slots <= MAX_COCKPIT_PILOTS:
            raise ValueError('invalid cockpit playback slot count')
        if trial_index is not None and cockpit_playback_slots > 1:
            raise ValueError('cannot combine single trial_index with multi-pilot cockpit slots')
        self._private_map_marker = private_map_marker
        self._roster_with_samples = roster_with_samples
        self._trial_index = trial_index
        self._cockpit_playback_slots = frozenset(COCKPIT_SLOTS[:cockpit_playback_slots])
        slot_names = (*COCKPIT_SLOTS[:cockpit_playback_slots], 'map') if cockpit_playback_slots > 1 else _SLOTS
        self._active_tracks = {}
        self._send = send
        self._set_views = None
        self._slots = {name: _View() for name in slot_names}
        self._subscribed = ()
        self._last_confirmed = None
        self._pilot_roster = ()
        self._roster_revision = 0
        self._next_roster_heartbeat = 0

    def set_pilot_roster(self, indices):
        """Give automatic cockpit playback a bounded list of pilot preferences."""
        if not self._cockpit_playback_slots or self._trial_index is not None:
            return
        if (type(indices) is not tuple or len(indices) > MAX_COCKPIT_PILOTS
                or any(type(index) is not int or not 1 <= index <= 200000
                       or index == self._own_index for index in indices)
                or tuple(sorted(set(indices))) != indices):
            return
        if indices != self._pilot_roster and self._roster_revision < MAX_COUNTER:
            self._pilot_roster = indices
            self._roster_revision += 1
            self._send_roster(time.monotonic())

    def _send_roster(self, now):
        self._next_roster_heartbeat = now + _ROSTER_HEARTBEAT
        self._send('|'.join(map(str, ('ROSTER', self._epoch, self._roster_revision, *self._pilot_roster))))

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

    def _end_slot(self, slot):
        track = self._active_tracks.pop(slot, None)
        if track is None:
            return
        serial, index, generation, sector = track
        # A still-queued native SEEN must not certify a pose after revocation.
        self._slots[slot].pending.clear()
        self._send(f'END|{slot}|{serial}|{self._epoch}|{index}|{generation}|{sector}')

    def expire(self):
        now = time.monotonic()
        if self._roster_revision and now >= self._next_roster_heartbeat:
            # The local UDP peer may bind after admission, and datagrams may be lost.
            self._send_roster(now)
        for slot, view in self._slots.items():
            if view.active is not None and now >= view.deadline:
                if slot in self._active_tracks:
                    self._end_slot(slot)
                view.replace(None)
        self._sync_views()

    def cockpit_binding(self):
        """Return the current immutable native cockpit binding, separate from map interest."""
        view = self._slots['cockpit']
        return view.active if time.monotonic() < view.deadline else None

    def _view(self, slot, view, serial, sector, index_text, actor, code):
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
            if slot in self._active_tracks:
                self._end_slot(slot)
            view.replace(binding)
            self._sync_views()

    def receive(self, fields):
        """Receive only from the source's authenticated, process-owned peer."""
        if self._set_views is None or len(fields) not in (8, 18, 21):
            return
        kind, slot, serial_text, epoch, *body = fields
        view = self._slots.get(slot)
        serial = _counter(serial_text)
        if view is None or serial is None or epoch != self._epoch:
            return
        self.expire()
        if kind == 'VIEW' and len(body) == 4:
            self._view(slot, view, serial, *body)
        elif (kind, len(body)) in (('SEEN', 14), ('SEEN4', 17)) and serial == view.serial and view.active is not None:
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
            if sample['version'] == 4:
                view.last_confirmed['orientation'] = sample['orientation']
            if velocity is not None and any(component != 0 for component in velocity.values()):
                view.last_nonzero_velocity_confirmed = view.last_confirmed
            return

    def snapshot(self, sample):
        # The X4 UDP module uses QPC on Windows, including with Python 3.11/3.12
        # where time.monotonic uses GetTickCount64 instead.
        received_at = time.perf_counter() if sys.platform == 'win32' else time.monotonic()
        self.expire()
        sample = motion_value(sample, allow_v4=True)
        if sample['version'] not in REAL_TIME_MOTION_VERSIONS or sample['epoch'] != self._epoch:
            return
        rate = sample['real_velocity']
        position = tuple(format(sample[axis], '.17g') for axis in 'xyz')
        velocity = ('-', '-', '-') if rate is None else tuple(format(rate[axis], '.17g') for axis in 'xyz')
        clocks = tuple(format(sample[key], '.17g') for key in ('source_time', 'capture_time'))
        values = (*position, *velocity, *clocks)
        angles = ()
        if sample['version'] == 4:
            angles = tuple(format(sample['orientation'][axis], '.17g') for axis in ORIENTATION_AXES)
        for slot, view in self._slots.items():
            if view.active is None or (sample['sector_macro'], sample['index']) != view.active[:2]:
                continue
            track = None
            is_timed = (
                (slot == 'map' and self._private_map_marker)
                or (slot == 'cockpit' and sample['index'] == self._trial_index)
                or (self._trial_index is None and slot in self._cockpit_playback_slots)
            )
            if is_timed:
                track = view.serial, sample['index'], sample['generation'], sample['sector_macro']
                if self._active_tracks.get(slot) != track:
                    self._end_slot(slot)
            local_clock = ()
            if track is not None:
                # The opt-in local consumer needs the original launcher
                # timestamp. The remote sender clock is a separate domain.
                local_clock = (format(received_at, '.17g'),)
            message = '|'.join(map(str, (
                'REMOTE4' if angles else 'REMOTE', slot, view.serial, self._epoch, sample['index'],
                sample['generation'], sample['sequence'], sample['sector_macro'], *values, *local_clock, *angles,
            )))
            if (self._roster_with_samples and slot in self._cockpit_playback_slots
                    and sample['index'] in self._pilot_roster):
                message += ROSTER_SAMPLE_SUFFIX + '|'.join(map(str, (self._roster_revision, *self._pilot_roster)))
            if self._send(message):
                view.pending.append((sample, (*values, *angles), time.monotonic()))
                if track is not None:
                    self._active_tracks[slot] = track

    def resume_route(self):
        """The single previously forwarded trial route, without pose or clocks."""
        track = self._active_tracks.get('cockpit')
        return track[1:] if track is not None else None

    def resume_routes(self):
        """All active cockpit playback routes, deduplicated by ship index."""
        by_index = {}
        for slot in COCKPIT_SLOTS:
            track = self._active_tracks.get(slot)
            if track is not None:
                by_index[track[1]] = track[1:]
        return [by_index[index] for index in sorted(by_index)]

    def transport_lost(self, index, generation):
        """Clear old receipts and revoke the optional map display on transport loss."""
        map_track = self._active_tracks.get('map')
        if map_track is not None and map_track[1:3] == (index, generation):
            self._end_slot('map')
        for view in self._slots.values():
            if view.active is not None and view.active[1] == index:
                view.pending = deque((item for item in view.pending if item[0]['generation'] != generation),
                                     maxlen=_MAX_PENDING)

    def end(self, index, generation):
        for slot, track in tuple(self._active_tracks.items()):
            if track[1:3] == (index, generation):
                self._end_slot(slot)
        self.transport_lost(index, generation)

    def reset_world(self):
        for slot in tuple(self._active_tracks):
            self._end_slot(slot)
        for view in self._slots.values():
            view.pending.clear()

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
