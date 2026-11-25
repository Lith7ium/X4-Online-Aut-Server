"""Bounded internal read-only ingress for the single native cockpit experiment."""
from collections.abc import Callable
from dataclasses import dataclass
import math
import re

from .company_motion_frames import MAX_COUNTER, view_matches

MEASUREMENT_KIND = 'npc_measurement'
RADAR_ENDED = object()
REGISTRY_EPOCH = '26a1a365c66788aa06f0aeb9e18ff62e'
PILOT_INDEX = 1073
NPC_INDEX = 943
NPC_CODE = 'MFQ-419'
SECTOR = 'cluster_06_sector001_macro'
MEASUREMENT_TIMEOUT = 2.0
MAX_MEASUREMENT_SEQUENCE = 160
_ACTOR = re.compile(r'0x[0-9a-f]{1,16}')
_ATTENTION = {'visible', 'inzone', 'nearby', 'adjacentroom', 'inroom'}


def validate_measurement_trial(enabled, *, base_game_only, observed_npc, pilot_index=PILOT_INDEX):
    """Recognize the supported native experiment before staging a game profile."""
    if type(enabled) is not bool or (enabled and (
            base_game_only is not True or observed_npc != (NPC_INDEX, NPC_CODE)
            or type(pilot_index) is not int or pilot_index != PILOT_INDEX)):
        raise ValueError('NPC measurement requires vanilla pilot 1073 observing 943 MFQ-419')


def _number(value, maximum):
    return type(value) in (int, float) and -maximum <= value <= maximum and math.isfinite(value)


def _vector(value, maximum):
    return type(value) is list and len(value) == 3 and all(_number(number, maximum) for number in value)


def measurement_value(value):
    fields = {'epoch', 'index', 'code', 'sector_macro', 'actor', 'sequence', 'attention',
              'position', 'game_time', 'capture_time', 'velocity_m_per_game_second'}
    if (type(value) is not dict or set(value) != fields
            or (value['epoch'], value['index'], value['code'], value['sector_macro'])
            != (REGISTRY_EPOCH, NPC_INDEX, NPC_CODE, SECTOR)
            or type(value['index']) is not int
            or type(value['sequence']) is not int or not 1 <= value['sequence'] <= MAX_MEASUREMENT_SEQUENCE
            or type(value['actor']) is not str or not _ACTOR.fullmatch(value['actor']) or int(value['actor'], 16) == 0
            or type(value['attention']) is not str or value['attention'] not in _ATTENTION
            or not _vector(value['position'], 1e7)
            or not _vector(value['velocity_m_per_game_second'], 1e5)
            or not _number(value['game_time'], 1e12) or value['game_time'] < 0
            or not _number(value['capture_time'], 1e12) or value['capture_time'] < 0):
        raise ValueError('invalid native NPC measurement')
    return {**value, 'position': list(value['position']),
            'velocity_m_per_game_second': list(value['velocity_m_per_game_second'])}


@dataclass
class NpcMeasurementIngress:
    """One authenticated pilot's reported radar, in one running motion window."""

    guard: Callable[[], bool]
    on_sample: Callable[[dict], None]
    peer: object = None
    window: int | None = None
    deadline: float | None = None
    latest: dict | None = None
    retired: bool = False
    retired_reason: str | None = None
    accepted: int = 0

    def __post_init__(self):
        if not callable(self.guard) or not callable(self.on_sample):
            raise ValueError('NPC measurement verification and sink must be callable')

    def bind(self, peer, grant):
        if self.retired or self.peer is not None or (grant.index, grant.epoch) != (PILOT_INDEX, REGISTRY_EPOCH):
            raise ValueError('NPC measurement pilot is unavailable')
        self.peer = peer

    def retire(self, reason):
        if self.retired:
            return
        self.retired = True
        self.retired_reason = reason
        self.peer = self.latest = None
        self.deadline = None

    def check(self, now):
        if self.deadline is None:
            return
        if now >= self.deadline:
            self.retire('timeout')
            return
        try:
            live = self.guard() is True
        except (OSError, RuntimeError, ValueError):
            live = False
        if not live:
            self.retire('provider_binding_lost')

    def view(self, peer, window, now):
        if peer is not self.peer:
            return
        visible = view_matches(peer.views, NPC_INDEX, SECTOR)
        if visible and self.window is None:
            self.window, self.deadline = window, now + MEASUREMENT_TIMEOUT
        elif not visible and self.window is not None:
            self.retire('view_lost')

    def receive(self, peer, message, window, now):
        self.check(now)
        if (self.retired or self.peer is not peer or self.window != window or self.deadline is None
                or not view_matches(peer.views, NPC_INDEX, SECTOR)
                or set(message) != {'kind', 'window', 'view_id', 'generation', 'sample'}
                or type(message['view_id']) is not int or not 1 <= message['view_id'] <= MAX_COUNTER
                or message['view_id'] != peer.view_id or message['generation'] != peer.grant.generation):
            raise ValueError('NPC measurement grant or radar view is unavailable')
        if message['sample'] is None:
            self.retire('radar_ended')
            return
        sample = measurement_value(message['sample'])
        previous = self.latest
        if previous is not None and (sample['actor'] != previous['actor']
                or sample['sequence'] <= previous['sequence'] or sample['capture_time'] <= previous['capture_time']
                or sample['game_time'] < previous['game_time']):
            raise ValueError('NPC measurement binding or sequence changed')
        self.latest = sample
        self.deadline = now + MEASUREMENT_TIMEOUT
        self.accepted += 1
        self.on_sample({'participant_handle': peer.grant.handle, 'pilot_index': peer.grant.index,
                        'generation': peer.grant.generation, 'window': window, 'view_id': peer.view_id,
                        'received_at': now, 'sample': measurement_value(sample)})
