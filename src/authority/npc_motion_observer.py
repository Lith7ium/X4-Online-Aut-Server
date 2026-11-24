"""Multi-ship two-view NPC observer over the existing CompanyMotionClient stream.

Coordinates independent high-attention (cockpit/sector) and open-map view slots
across up to two sectors and 256 subscribed indices (matching CompanyMotionFrames),
tracks explicit interest withdrawal and reacquisition per ship, retains the last
confirmed sample and last confirmed nonzero-velocity sample per ship, and invokes
optional local playback/end callbacks without altering player-ship grants.
"""
from copy import deepcopy
from dataclasses import dataclass, field
import re
import time

from .company_motion_frames import MAX_INDICES, SECTOR, normalize_views
from .experimental_tcp_motion import motion_value

_HEX32 = re.compile(r'[0-9a-f]{32}')
_SLOTS = ('high_attention', 'map')


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
