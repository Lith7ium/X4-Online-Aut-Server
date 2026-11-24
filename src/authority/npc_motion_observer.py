"""Multi-ship two-view NPC observer over the existing CompanyMotionClient stream.

Coordinates independent high-attention (cockpit/sector) and open-map view slots
across up to two sectors and 256 subscribed indices (matching CompanyMotionFrames),
tracks explicit interest withdrawal and reacquisition per ship, retains the last
confirmed sample and last confirmed nonzero-velocity sample per ship, and invokes
optional local playback/end callbacks without altering player-ship grants.
"""
from copy import deepcopy
from dataclasses import dataclass, field
import math
import re
import secrets
import socket
import threading
import time

from .company_motion_frames import MAX_INDICES, SECTOR, normalize_views
from .experimental_tcp_motion import motion_value

_HEX32 = re.compile(r'[0-9a-f]{32}')
_SLOTS = ('high_attention', 'map')
MAX_NPC_DATAGRAM = 768


@dataclass
class _TrackedNpc:
    index: int
    last_sector: str | None = None
    last_generation: str | None = None
    last_sequence: int = 0
    samples_confirmed: int = 0
    velocity_samples_confirmed: int = 0
    last_confirmed: dict | None = None
    last_nonzero_velocity_confirmed: dict | None = None
    withdrawal_count: int = 0
    reacquisition_count: int = 0
    end_count: int = 0
    transport_loss_count: int = 0
    currently_visible: bool = False
    was_withdrawn: bool = False
    reacquired_after_withdrawal: bool = False
    reacquired_after_sector_change: bool = False


class NpcMotionObserver:
    """Manage multi-ship NPC subscriptions, retention, withdrawal, and reacquisition.

    Attach `on_snapshot`, `on_end`, `on_transport_lost`, and `on_view_reset` to a
    real `CompanyMotionClient` (or `CompanyMotionReconnect`), bind its `set_views`
    callable via `bind_views(client.set_views)`, then update `high_attention` and
    `map` views independently as local visibility changes.
    """

    def __init__(self, registry_epoch, own_index, *, on_playback=None, on_playback_end=None):
        if not isinstance(registry_epoch, str) or not _HEX32.fullmatch(registry_epoch):
            raise ValueError('valid 32-hex registry epoch required')
        if type(own_index) is not int or not 1 <= own_index <= 200000:
            raise ValueError('valid own ship index required')
        if on_playback is not None and not callable(on_playback):
            raise ValueError('on_playback must be callable')
        if on_playback_end is not None and not callable(on_playback_end):
            raise ValueError('on_playback_end must be callable')
        self._epoch = registry_epoch
        self._own_index = own_index
        self._on_playback = on_playback
        self._on_playback_end = on_playback_end
        self._set_views = None
        self._slot_views = {slot: None for slot in _SLOTS}  # slot -> (sector_macro, frozenset(indices)) | None
        self._subscribed = {}  # sector_macro -> frozenset(indices)
        self._tracks = {}  # index -> _TrackedNpc
        self._snapshots_total = 0
        self._velocity_snapshots_total = 0
        self._discarded_offscreen = 0
        self._world_resets = 0

    @property
    def snapshots_total(self):
        return self._snapshots_total

    def bind_views(self, set_views):
        if not callable(set_views):
            raise ValueError('set_views callable required')
        self._set_views = set_views
        self._sync_views(force=True)

    def set_slot_view(self, slot, sector_macro, indices):
        """Replace one view slot ('high_attention' or 'map'). Empty indices clears the slot."""
        if slot not in _SLOTS:
            raise ValueError('unknown observer slot: ' + str(slot))
        if not indices:
            new_slot = None
        else:
            if type(sector_macro) is not str or not SECTOR.fullmatch(sector_macro):
                raise ValueError('invalid sector macro')
            filtered = sorted({int(i) for i in indices} - {self._own_index})
            if not filtered:
                new_slot = None
            else:
                if len(filtered) > MAX_INDICES or any(not 1 <= i <= 200000 for i in filtered):
                    raise ValueError('invalid NPC indices for slot')
                new_slot = (sector_macro, frozenset(filtered))
        previous_slot = self._slot_views[slot]
        self._slot_views[slot] = new_slot
        try:
            self._sync_views()
        except BaseException:
            self._slot_views[slot] = previous_slot
            raise

    def clear_slot(self, slot):
        self.set_slot_view(slot, None, [])

    def _combined_rows(self):
        rows = []
        for slot in _SLOTS:
            entry = self._slot_views[slot]
            if entry is not None:
                sector, indices = entry
                rows.append({'sector_macro': sector, 'indices': sorted(indices)})
        return rows

    def _active_sectors_by_index(self, views):
        by_index = {}
        for sector, indices in views.items():
            for idx in indices:
                by_index.setdefault(idx, set()).add(sector)
        return by_index

    def _sync_views(self, *, force=False):
        rows = self._combined_rows()
        normalized = normalize_views(rows, own_index=self._own_index)
        if not force and normalized == self._subscribed:
            return
        old_by_index = self._active_sectors_by_index(self._subscribed)
        new_by_index = self._active_sectors_by_index(normalized)
        for idx in set(old_by_index) - set(new_by_index):
            track = self._tracks.get(idx)
            if track is not None and track.currently_visible:
                track.currently_visible = False
                track.was_withdrawn = True
                track.withdrawal_count += 1
                if self._on_playback_end is not None:
                    self._on_playback_end(idx, track.last_generation, 'withdrawn')
        for idx, sectors in new_by_index.items():
            track = self._tracks.setdefault(idx, _TrackedNpc(index=idx))
            if not track.currently_visible:
                track.currently_visible = True
                if track.was_withdrawn:
                    track.reacquisition_count += 1
                    track.reacquired_after_withdrawal = True
                    if track.last_sector is not None and track.last_sector not in sectors:
                        track.reacquired_after_sector_change = True
                    track.was_withdrawn = False
        self._subscribed = normalized
        if self._set_views is not None:
            wire_rows = [{'sector_macro': sector, 'indices': sorted(indices)}
                         for sector, indices in sorted(normalized.items())]
            self._set_views(wire_rows)

    def on_snapshot(self, sample):
        """CompanyMotionClient on_snapshot callback."""
        value = motion_value(sample)
        if value['version'] != 3 or value['epoch'] != self._epoch:
            return
        index = value['index']
        sector = value['sector_macro']
        if index == self._own_index or index not in self._subscribed.get(sector, ()):
            self._discarded_offscreen += 1
            return
        track = self._tracks.setdefault(index, _TrackedNpc(index=index, currently_visible=True))
        if track.last_sector is not None and track.last_sector != sector:
            track.reacquired_after_sector_change = True
        track.last_sector = sector
        track.last_generation = value['generation']
        track.last_sequence = value['sequence']
        track.samples_confirmed += 1
        self._snapshots_total += 1
        velocity = value['real_velocity']
        if velocity is not None:
            track.velocity_samples_confirmed += 1
            self._velocity_snapshots_total += 1
        record = {
            'epoch': value['epoch'],
            'index': index,
            'generation': value['generation'],
            'sequence': value['sequence'],
            'sector_macro': sector,
            'position': {'x': value['x'], 'y': value['y'], 'z': value['z']},
            'real_velocity': deepcopy(velocity),
            'source_time': value['source_time'],
            'capture_time': value['capture_time'],
            'received_at': time.monotonic(),
        }
        track.last_confirmed = record
        if velocity is not None and any(component != 0 for component in velocity.values()):
            track.last_nonzero_velocity_confirmed = record
        if self._on_playback is not None:
            self._on_playback(deepcopy(value), record)

    def on_end(self, index, generation):
        """CompanyMotionClient on_end callback (sector change, retirement, or pause)."""
        track = self._tracks.get(index)
        if track is not None:
            track.end_count += 1
            if self._on_playback_end is not None:
                self._on_playback_end(index, generation, 'end')

    def on_transport_lost(self, index, generation):
        """CompanyMotionClient on_transport_lost callback."""
        track = self._tracks.get(index)
        if track is not None:
            track.transport_loss_count += 1
            if self._on_playback_end is not None:
                self._on_playback_end(index, generation, 'transport_lost')

    def on_view_reset(self):
        """CompanyMotionClient on_view_reset callback (after pause/save/resume clock reset)."""
        self._world_resets += 1
        for idx, track in self._tracks.items():
            if self._on_playback_end is not None and track.last_generation is not None:
                self._on_playback_end(idx, track.last_generation, 'world_reset')
        self._sync_views(force=True)

    def stats(self):
        return deepcopy({
            'snapshots_total': self._snapshots_total,
            'velocity_snapshots_total': self._velocity_snapshots_total,
            'discarded_offscreen': self._discarded_offscreen,
            'world_resets': self._world_resets,
            'subscribed': {sector: sorted(indices) for sector, indices in sorted(self._subscribed.items())},
            'tracks': {
                idx: {
                    'samples_confirmed': t.samples_confirmed,
                    'velocity_samples_confirmed': t.velocity_samples_confirmed,
                    'last_sector': t.last_sector,
                    'last_generation': t.last_generation,
                    'last_sequence': t.last_sequence,
                    'last_confirmed': t.last_confirmed,
                    'last_nonzero_velocity_confirmed': t.last_nonzero_velocity_confirmed,
                    'withdrawal_count': t.withdrawal_count,
                    'reacquisition_count': t.reacquisition_count,
                    'end_count': t.end_count,
                    'transport_loss_count': t.transport_loss_count,
                    'currently_visible': t.currently_visible,
                    'reacquired_after_withdrawal': t.reacquired_after_withdrawal,
                    'reacquired_after_sector_change': t.reacquired_after_sector_change,
                }
                for idx, t in sorted(self._tracks.items())
            },
        })


class NpcLocalPlaybackBridge:
    """Authenticated loopback UDP bridge between NpcMotionObserver and native X4 Lua playback.

    Mirrors CompanyMotionObserver's local peer-ownership discipline: only packets
    bearing the exact lease/epoch prefix from the validated local X4 peer are
    accepted, and outbound REMOTE/LOSS/END frames originate strictly from the
    admitted CompanyMotionClient -> NpcMotionObserver callbacks.
    """

    def __init__(self, registry_epoch, own_index, validate_peer, *, max_readbacks=512):
        if not isinstance(registry_epoch, str) or not _HEX32.fullmatch(registry_epoch):
            raise ValueError('valid 32-hex registry epoch required')
        if type(own_index) is not int or not 1 <= own_index <= 200000:
            raise ValueError('valid own ship index required')
        if not callable(validate_peer):
            raise ValueError('peer validator callable required')
        self._thread = threading.get_ident()
        self._epoch = registry_epoch
        self._own_index = own_index
        self._validate_peer = validate_peer
        self._lease = secrets.token_hex(16)
        self._prefix = f'N|{self._lease}|{self._epoch}|'.encode('ascii')
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            self._socket.bind(('127.0.0.1', 0))
            self._socket.setblocking(False)
        except BaseException:
            self._socket.close()
            raise
        self._port = self._socket.getsockname()[1]
        self._peer = None
        self._peer_bound_at = None
        self._view_serials = {slot: 0 for slot in _SLOTS}
        self._max_readbacks = max(16, int(max_readbacks))
        self._remote_sent = 0
        self._loss_sent = 0
        self._end_sent = 0
        self._seen_by_x4 = {}
        self._readbacks = []
        self._events = []
        self._observer = NpcMotionObserver(
            registry_epoch, own_index,
            on_playback=self._send_remote,
            on_playback_end=self._send_end_or_loss,
        )

    @property
    def port(self):
        return self._port

    @property
    def lease(self):
        return self._lease

    @property
    def epoch(self):
        return self._epoch

    @property
    def socket(self):
        return self._socket

    @property
    def peer(self):
        return self._peer

    @property
    def observer(self):
        return self._observer

    def _check_owner(self):
        if threading.get_ident() != self._thread:
            raise RuntimeError('exclusive NPC playback bridge owner thread required')

    def _send(self, payload):
        self._check_owner()
        if self._peer is None:
            return False
        packet = self._prefix + payload.encode('ascii')
        if len(packet) > MAX_NPC_DATAGRAM:
            return False
        try:
            if self._validate_peer(self._peer) is not True:
                return False
            return self._socket.sendto(packet, self._peer) == len(packet)
        except OSError:
            return False

    def _send_remote(self, sample, record):
        vel = sample.get('real_velocity')
        if vel is None:
            vx, vy, vz = '-', '-', '-'
        else:
            vx = format(vel['x'], '.17g')
            vy = format(vel['y'], '.17g')
            vz = format(vel['z'], '.17g')
        fields = (
            'REMOTE',
            str(sample['index']),
            sample['generation'],
            str(sample['sequence']),
            sample['sector_macro'],
            format(sample['x'], '.17g'),
            format(sample['y'], '.17g'),
            format(sample['z'], '.17g'),
            vx, vy, vz,
            format(sample['source_time'], '.17g'),
            format(sample['capture_time'], '.17g'),
            format(record['received_at'], '.17g'),
        )
        if self._send('|'.join(fields)):
            self._remote_sent += 1

    def _send_end_or_loss(self, index, generation, reason):
        gen = generation or ('0' * 32)
        if reason == 'transport_lost':
            if self._send(f'LOSS|{index}|{gen}|{reason}'):
                self._loss_sent += 1
        else:
            if self._send(f'END|{index}|{gen}|{reason}'):
                self._end_sent += 1

    def send_command(self, kind, *args):
        """Send an explicit control/probe packet (e.g., PROBE_50M) over the authenticated link."""
        return self._send('|'.join((str(kind), *(str(a) for a in args))))

    def poll(self):
        """Drain authenticated X4 Lua messages (HELLO, VIEW, SEEN, READBACK, EVENT)."""
        self._check_owner()
        processed = 0
        for _ in range(64):
            try:
                raw, address = self._socket.recvfrom(MAX_NPC_DATAGRAM + 1)
            except BlockingIOError:
                break
            except OSError as error:
                if getattr(error, 'winerror', None) == 10040:
                    continue
                raise
            if len(raw) > MAX_NPC_DATAGRAM or address[0] != '127.0.0.1' or not raw.startswith(self._prefix):
                continue
            if self._peer is not None and address != self._peer:
                continue
            if self._peer is None:
                if self._validate_peer(address) is not True:
                    continue
                self._peer = address
                self._peer_bound_at = time.monotonic()
            try:
                fields = raw[len(self._prefix):].decode('ascii').split('|')
            except UnicodeDecodeError:
                continue
            if not fields:
                continue
            kind = fields[0]
            processed += 1
            if kind == 'HELLO' and len(fields) == 1:
                self._send('ACK|HELLO')
            elif kind == 'VIEW' and len(fields) == 5:
                _, slot, serial_text, sector, indices_text = fields
                if slot not in _SLOTS or not serial_text.isdigit():
                    continue
                serial = int(serial_text)
                if serial <= self._view_serials[slot]:
                    continue
                if sector == '-' or indices_text in ('-', '', '0'):
                    self._view_serials[slot] = serial
                    self._observer.clear_slot(slot)
                elif SECTOR.fullmatch(sector):
                    parts = [p for p in indices_text.split(',') if p]
                    if parts and all(p.isdigit() for p in parts):
                        indices = [int(p) for p in parts]
                        if all(1 <= i <= 200000 for i in indices) and len(indices) <= MAX_INDICES:
                            self._view_serials[slot] = serial
                            self._observer.set_slot_view(slot, sector, indices)
            elif kind == 'SEEN' and len(fields) == 4:
                _, idx_text, gen, seq_text = fields
                if idx_text.isdigit() and seq_text.isdigit() and _HEX32.fullmatch(gen):
                    idx, seq = int(idx_text), int(seq_text)
                    entry = self._seen_by_x4.setdefault(idx, {'count': 0, 'last_sequence': 0, 'generation': gen})
                    entry['count'] += 1
                    entry['generation'] = gen
                    if seq > entry['last_sequence']:
                        entry['last_sequence'] = seq
            elif kind == 'READBACK' and len(fields) == 16:
                try:
                    rec = {
                        'kind': fields[1],
                        'index': int(fields[2]),
                        'generation': fields[3],
                        'sequence': int(fields[4]),
                        'phase': fields[5],
                        'elapsed': float(fields[6]),
                        'error': float(fields[7]),
                        'actual': {'x': float(fields[8]), 'y': float(fields[9]), 'z': float(fields[10])},
                        'target': {'x': float(fields[11]), 'y': float(fields[12]), 'z': float(fields[13])},
                        'game_time': float(fields[14]),
                        'local_time': float(fields[15]),
                    }
                except ValueError:
                    continue
                if all(math.isfinite(v) for v in (
                    rec['elapsed'], rec['error'], rec['game_time'], rec['local_time'],
                    *rec['actual'].values(), *rec['target'].values(),
                )):
                    if len(self._readbacks) < self._max_readbacks:
                        self._readbacks.append(rec)
            elif kind == 'EVENT' and len(fields) >= 4:
                _, ev_kind, idx_text, detail = fields[0], fields[1], fields[2], '|'.join(fields[3:])
                if idx_text.isdigit():
                    self._events.append({
                        'kind': ev_kind,
                        'index': int(idx_text),
                        'detail': detail,
                        'at': time.monotonic(),
                    })
        return processed

    def stats(self):
        self._check_owner()
        return deepcopy({
            'port': self._port,
            'peer_bound': self._peer is not None,
            'remote_sent': self._remote_sent,
            'loss_sent': self._loss_sent,
            'end_sent': self._end_sent,
            'seen_by_x4': self._seen_by_x4,
            'readback_count': len(self._readbacks),
            'readbacks': self._readbacks,
            'events': self._events,
            'observer': self._observer.stats(),
        })

    def close(self):
        self._check_owner()
        self._socket.close()
