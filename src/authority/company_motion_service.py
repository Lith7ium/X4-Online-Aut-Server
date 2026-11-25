"""Local backend for the pinned TLS movement service, polled by the world owner."""
from collections import deque
from collections.abc import Callable
import copy
from dataclasses import asdict, dataclass, field
import math
import re
import secrets
import socket
import threading
import time

from .company_limits import MAX_COCKPIT_PLAYBACK_PILOTS
from .company_ships import _DISPLAY_CODE
from .company_npc_measurement import MEASUREMENT_KIND, NpcMeasurementIngress
from .experimental_tcp_motion import check_motion_progress, motion_value
from .company_motion_frames import (MAX_PEERS, MAX_COUNTER, SECTOR, encode_frame,
                                    normalize_views, read_frames, view_matches)

_HEX32 = re.compile(r'[0-9a-f]{32}')
_TICKET = re.compile(r'[0-9a-f]{64}')
# A stopped game sampler cannot refresh a new viewer from an indefinitely old pose.
MAX_SAMPLE_AGE = 3.0


@dataclass
class _MotionTraffic:
    channel_bindings: int = 0
    samples_accepted: int = 0
    samples_with_velocity: int = 0
    max_source_step_metres: float | None = None
    max_reported_speed_mps: float | None = None
    view_updates: int = 0
    nonempty_view_updates: int = 0
    snapshot_receipts: int = 0
    last_received_ship_index: int | None = None

    def record_source(self, sample, previous):
        self.samples_accepted += 1
        velocity = sample['real_velocity']
        if velocity is not None:
            self.samples_with_velocity += 1
            speed = math.hypot(velocity['x'], velocity['y'], velocity['z'])
            if self.max_reported_speed_mps is None or speed > self.max_reported_speed_mps:
                self.max_reported_speed_mps = speed

        if previous is not None and previous['sector_macro'] == sample['sector_macro']:
            step = math.hypot(sample['x'] - previous['x'], sample['y'] - previous['y'],
                              sample['z'] - previous['z'])
            if self.max_source_step_metres is None or step > self.max_source_step_metres:
                self.max_source_step_metres = step


@dataclass
class _Grant:
    handle: int
    ticket: str = field(repr=False)
    index: int
    epoch: str
    generation: str
    last_sample: dict | None = None
    last_accepted: dict | None = None
    last_sequence: int = 0
    sector_revision: int = 0
    visible_until: float = 0
    trial_route: tuple | None = None
    cockpit_routes: dict = field(default_factory=dict)
    traffic: _MotionTraffic = field(default_factory=_MotionTraffic)

    def traffic_summary(self):
        return {'participant_handle': self.handle, 'ship_index': self.index,
                'last_source_sequence': self.last_sequence, **asdict(self.traffic)}


@dataclass
class _Peer:
    connection: socket.socket
    bind_deadline: float
    grant: _Grant | None = None
    incoming: bytearray = field(default_factory=bytearray, repr=False)
    outgoing: bytes = field(default=b'', repr=False)
    notices: deque = field(default_factory=deque)
    latest: dict = field(default_factory=dict)
    delivered: dict = field(default_factory=dict)
    hidden: set = field(default_factory=set)
    trial_index: int | None = None
    cockpit_playback_limit: int = 0
    pilot_roster: bool = False
    last_pilot_roster: tuple | None = None
    resuming: tuple | None = None
    resuming_routes: dict = field(default_factory=dict)
    awaiting_ack: tuple | None = None
    source_ack: bytes = b''
    view_id: int = 0
    views: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AcceptedCompanyMotion:
    """One transport-accepted V3 sample, in the server's receive clock domain."""

    handle: int
    window: int
    accepted_at: float
    _sample: dict = field(repr=False)
    sector_revision: int = 0

    @property
    def index(self):
        return self._sample['index']

    @property
    def sample(self):
        return copy.deepcopy(self._sample)


@dataclass(frozen=True, slots=True)
class NPCMotionBinding:
    """One provider-owned observation lifetime, without pilot authorization."""

    epoch: str
    index: int
    ship_code: str
    generation: str
    window: int


@dataclass
class _NPCObservation:
    binding: NPCMotionBinding
    reservation_guard: Callable[[], bool] = field(repr=False)
    last_sample: dict | None = None
    visible_until: float = 0


class CompanyMotionService:
    """Authorize a stream for one admitted ship and coalesce unsent views by ship.

    All methods run on the supervisor thread. The TLS gateway on the same public
    port connects only to this loopback listener. Control-session cleanup revokes
    its grant; a movement socket closure cannot remove a player from the world.
    A source acknowledgement confirms transport acceptance. The world owner
    separately takes and settles samples before any native pause and save.
    """
    def __init__(self, *, application_drops=None, npc_measurement=None):
        if npc_measurement is not None and type(npc_measurement) is not NpcMeasurementIngress:
            raise ValueError('invalid private NPC measurement ingress')
        self._npc_measurement = npc_measurement
        self._application_drops = application_drops
        if application_drops is not None:
            application_drops.update(last_reason=None, by_reason={})
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.listener.bind(('127.0.0.1', 0))
            self.listener.listen(MAX_PEERS)
            self.listener.setblocking(False)
        except BaseException:
            self.listener.close()
            raise
        self.port = self.listener.getsockname()[1]
        self._grants = {}
        self._closed_traffic = deque(maxlen=MAX_PEERS)
        self._closed_grants = 0
        self._tickets = {}
        self._peers = {}
        self._owner = threading.current_thread()
        self._application_pending = {}
        self._application_batch = {}
        self._npc_observation = None
        self.running = False
        self.window = 0
        self.accepted_samples = 0
        self.samples_with_velocity = 0
        self.last_ship_position = None

    def install_npc_measurement(self, measurement):
        """Install the private receiver before any participant admission."""
        self._check_owner()
        if (type(measurement) is not NpcMeasurementIngress or self._npc_measurement is not None
                or self.running or self._grants or self._peers):
            raise ValueError('private NPC measurement must be configured before admission')
        self._npc_measurement = measurement

    def admit(self, handle, attempt_id, ship_index, sector_macro, session_epoch, registry_epoch):
        self._check_owner()
        if (type(handle) is not int or handle < 1 or handle in self._grants or
                len(self._grants) >= MAX_PEERS or
                type(ship_index) is not int or not 1 <= ship_index <= 200000 or
                any(not isinstance(token, str) or not _HEX32.fullmatch(token)
                    for token in (attempt_id, session_epoch, registry_epoch)) or
                not isinstance(sector_macro, str) or not SECTOR.fullmatch(sector_macro) or
                any(grant.index == ship_index for grant in self._grants.values())):
            raise ValueError('invalid participant movement grant')
        # A previous source may have left over a failed control link. A new
        # owner ends its remaining route metadata before receiving authority.
        observation = self._npc_observation
        if observation is not None and observation.binding.index == ship_index:
            self.withdraw_npc_source(observation.binding)
        else:
            self._retire_sample(ship_index)
        grant = _Grant(handle, secrets.token_hex(32), ship_index, registry_epoch, secrets.token_hex(16))
        # A previous grant's sealed sample cannot move a reassigned ship.
        pending = self._application_pending.pop(ship_index, None)
        if pending is not None:
            self._record_drop('queued_reassigned', pending, time.monotonic())
        self._application_batch.pop(ship_index, None)
        self._grants[handle] = self._tickets[grant.ticket] = grant
        return {'ticket': grant.ticket, 'epoch': grant.epoch, 'generation': grant.generation,
                'index': ship_index, 'session_epoch': session_epoch}

    def register_npc_source(self, registry_epoch, ship_index, ship_code, *, reservation_guard):
        """Register one trusted local observation for the current running world.

        The provider verifies the registry identity and supplies a read-only
        guard that returns True while the NPC remains verified and unreserved.
        """
        self._check_owner()
        if (not self.running or self.listener.fileno() < 0 or self._npc_observation is not None
                or type(registry_epoch) is not str or not _HEX32.fullmatch(registry_epoch)
                or type(ship_index) is not int or not 1 <= ship_index <= 200000
                or type(ship_code) is not str or not _DISPLAY_CODE.fullmatch(ship_code)
                or not callable(reservation_guard)
                or any(grant.index == ship_index for grant in self._grants.values())
                or ship_index in self._application_pending or ship_index in self._application_batch
                or reservation_guard() is not True):
            raise ValueError('invalid or unavailable provider NPC observation')
        binding = NPCMotionBinding(registry_epoch, ship_index, ship_code, secrets.token_hex(16), self.window)
        # A recently departed pilot may still have a retained visibility route.
        self._retire_sample(ship_index)
        self._npc_observation = _NPCObservation(binding, reservation_guard)
        return binding

    def _current_npc(self):
        observation = self._npc_observation
        if observation is None:
            return None
        try:
            allowed = observation.reservation_guard() is True
        except Exception:
            self.withdraw_npc_source(observation.binding)
            raise
        if not allowed:
            self.withdraw_npc_source(observation.binding)
            return None
        return observation

    def publish_npc_sample(self, binding, sample):
        """Publish an unmodified V3 observation captured on the provider host."""
        self._check_owner()
        observation = self._npc_observation
        if observation is None or observation.binding is not binding or self._current_npc() is None:
            return False
        value = motion_value(sample)
        if (value['version'], value['index'], value['epoch'], value['generation']) != (
                3, binding.index, binding.epoch, binding.generation):
            raise ValueError('sample does not match this NPC observation')
        now = time.monotonic()
        previous = observation.last_sample
        if (not 0 <= now - value['capture_time'] < MAX_SAMPLE_AGE
                or (previous is not None and value['sequence'] <= previous['sequence'])):
            return False
        check_motion_progress(value, previous)
        if previous is not None and previous['sector_macro'] != value['sector_macro']:
            self._retire_sample(binding.index, binding.generation)
        observation.last_sample = value
        observation.visible_until = value['capture_time'] + MAX_SAMPLE_AGE
        self._fan_out(value)
        return True

    def withdraw_npc_source(self, binding):
        """Retire this exact observation without touching pilot authority."""
        self._check_owner()
        observation = self._npc_observation
        if observation is None or observation.binding is not binding:
            return False
        self._npc_observation = None
        self._retire_sample(binding.index, binding.generation)
        return True

    def _drop(self, peer, *, transport_lost=True):
        if self._peers.pop(peer.connection, None) is None:
            return
        peer.connection.close()
        if self._npc_measurement is not None and self._npc_measurement.peer is peer:
            self._npc_measurement.retire('socket_closed')
        grant = peer.grant
        if (grant is not None and self._grants.get(grant.handle) is grant
                and (grant.last_sample is not None or not transport_lost)):
            if not transport_lost:
                grant.trial_route = None
                grant.cockpit_routes.clear()
            self._retire_sample(grant.index, grant.generation, transport_lost=transport_lost)
            grant.last_sample = None

    def _notice(self, peer, message):
        if len(peer.notices) >= 2 * MAX_PEERS:
            self._drop(peer, transport_lost=False)
        else:
            peer.notices.append(encode_frame(message))

    def _retire_sample(self, index, generation=None, *, transport_lost=False):
        if not transport_lost:
            for viewer_grant in self._grants.values():
                route = viewer_grant.trial_route
                if route is not None and route[0] == index and (generation is None or route[1] == generation):
                    viewer_grant.trial_route = None
                croute = viewer_grant.cockpit_routes.get(index)
                if croute is not None and (generation is None or croute[1] == generation):
                    del viewer_grant.cockpit_routes[index]
        for viewer in tuple(self._peers.values()):
            viewer.latest.pop(index, None)
            delivered = viewer.delivered.get(index)
            if delivered is None or (generation is not None and delivered[0] != generation):
                continue
            kind = 'motion_end'
            if transport_lost and (viewer.trial_index == index or viewer.cockpit_playback_limit > 0):
                if index in viewer.hidden:
                    continue
                # Retain only the delivered route until the view or authority
                # ends. The receiver independently bounds pose extrapolation.
                viewer.hidden.add(index)
                kind = 'motion_hidden'
            else:
                del viewer.delivered[index]
                viewer.hidden.discard(index)
            self._notice(viewer, {'kind': kind, 'window': self.window, 'view_id': viewer.view_id,
                                  'index': index, 'generation': delivered[0]})

    def revoke(self, handle):
        self._remove_grant(handle, transport_lost=False)

    def transport_lost(self, handle):
        """Immediately release a disconnected control participant's authority."""
        self._remove_grant(handle, transport_lost=True)

    def _remove_grant(self, handle, *, transport_lost):
        self._check_owner()
        grant = self._grants.pop(handle, None)
        if grant is None:
            return
        self._closed_traffic.append(grant.traffic_summary())
        self._closed_grants += 1
        self._tickets.pop(grant.ticket)
        for peer in tuple(self._peers.values()):
            if peer.grant is grant:
                self._drop(peer)
        self._retire_sample(grant.index, grant.generation, transport_lost=transport_lost)

    def participant_traffic(self):
        """Report transport evidence for active grants and the most recent departures.

        A snapshot receipt comes from the remote launcher. Native X4 playback
        and position readback are recorded separately by the receiving game.
        """
        self._check_owner()
        return {
            'active': [grant.traffic_summary() for grant in self._grants.values()],
            'recently_closed': [row.copy() for row in self._closed_traffic],
            'older_grants_omitted': self._closed_grants - len(self._closed_traffic),
        }

    def set_running(self, running):
        self._check_owner()
        if type(running) is not bool:
            raise ValueError('invalid movement clock state')
        if self.running == running:
            return
        if not running and self._npc_measurement is not None and self._npc_measurement.peer is not None:
            self._npc_measurement.retire('motion_window_ended')
        self.running = running
        self.window += 1
        self._application_pending.clear()
        self._application_batch.clear()
        self._npc_observation = None
        for grant in self._grants.values():
            grant.last_sample = None
            grant.visible_until = 0
            grant.trial_route = None
            grant.cockpit_routes.clear()
        for peer in tuple(self._peers.values()):
            if peer.grant is None:
                continue
            peer.latest.clear()
            peer.delivered.clear()
            peer.hidden.clear()
            peer.resuming = None
            peer.resuming_routes.clear()
            peer.views.clear()
            peer.last_pilot_roster = None
            peer.awaiting_ack, peer.source_ack, peer.view_id = None, b'', 0
            self._notice(peer, {'kind': 'motion_state', 'window': self.window, 'running': running})

    def _check_owner(self):
        if threading.current_thread() is not self._owner:
            raise RuntimeError('exclusive company motion service owner required')

    @staticmethod
    def _fresh(record, now):
        return 0 <= now - record.accepted_at < MAX_SAMPLE_AGE

    def _record_drop(self, reason, record, now):
        """Keep the last receipt and total per reason in the optional provider result."""
        if self._application_drops is None:
            return
        drops = self._application_drops['by_reason']
        previous = drops.get(reason)
        self._application_drops['last_reason'] = reason
        drops[reason] = {
            'count': previous['count'] + 1 if previous else 1,
            'last_ship_index': record.index, 'last_sequence': record._sample['sequence'],
            'last_age_seconds': now - record.accepted_at,
        }

    def _remember_application(self, record):
        pending = self._application_pending
        if record.index not in pending and len(pending) >= MAX_PEERS:
            # Active grants themselves are bounded. Churn can fill the queue
            # with departed grants, so keep active samples ahead of older ones.
            active = {grant.index for grant in self._grants.values()}
            oldest_departed = next(index for index in pending if index not in active)
            self._record_drop('queued_evicted', pending.pop(oldest_departed), record.accepted_at)
        pending[record.index] = record  # Replacement retains the order of waiting ships.

    def take_application_batch(self, limit=1):
        """Take at most one fresh latest sample per ship while running.

        The synchronous consumer settles this batch before taking another or
        freezing. Revoked grants can contribute only data accepted before EOF.
        """
        self._check_owner()
        if type(limit) is not int or not 1 <= limit <= MAX_PEERS:
            raise ValueError('invalid motion application batch size')
        if not self.running:
            return ()
        pending = self._application_pending
        now = time.monotonic()
        batch = {}
        for index, record in tuple(pending.items()):
            if not self._fresh(record, now):
                self._record_drop('queued_expired', pending.pop(index), now)
            elif len(batch) < limit:
                batch[index] = pending.pop(index)
        self._application_batch = batch
        return tuple(batch.values())

    def freeze_application(self):
        """Fence ingress and return the last fresh pending data before engine pause.

        Call once after settling the running batch, before the native pause or
        save. Only samples previously accepted by this service can survive the
        fence. A subsequent running window invalidates the whole frozen batch.
        """
        self._check_owner()
        if not self.running:
            return ()
        batch = self.take_application_batch(MAX_PEERS)
        self.set_running(False)
        self._application_batch = {record.index: record for record in batch}
        return batch

    def is_application_current(self, record, *, after_inspect=None):
        """Check the running/frozen batch. A pose consumer tags its actual discard point."""
        self._check_owner()
        if type(record) is not AcceptedCompanyMotion:
            return False
        now = time.monotonic()
        if (self._application_batch.get(record.index) is not record or
                record.window != self.window - int(not self.running)):
            reason = 'invalidated'
        elif not self._fresh(record, now):
            reason = 'expired'
        else:
            return True
        if after_inspect is not None:
            stage = 'after_inspect' if after_inspect else 'before_inspect'
            self._record_drop(f'{stage}_{reason}', record, now)
        return False

    def _bind(self, peer, message):
        ticket = message.get('ticket')
        fields = set(message)
        allowed = {'kind', 'ticket', 'loss_notifications', 'trial_index', 'resume_route',
                   'cockpit_playback_limit', 'resume_routes', 'pilot_roster', MEASUREMENT_KIND}
        if (fields - allowed or message.get('kind') != 'bind_motion'
                or type(ticket) is not str or not _TICKET.fullmatch(ticket)):
            raise ValueError('invalid movement binding')
        grant = self._tickets.get(ticket)
        if grant is None or any(other.grant is grant for other in self._peers.values()):
            raise ValueError('movement ticket unavailable')
        trial_index = message.get('trial_index')
        cockpit_limit = message.get('cockpit_playback_limit')
        if (fields & {'trial_index', 'resume_route'}) and (fields & {'cockpit_playback_limit', 'resume_routes'}):
            raise ValueError('cannot combine single trial_index with multi-pilot cockpit playback')
        if 'loss_notifications' in fields and trial_index is None and cockpit_limit is None:
            raise ValueError('loss_notifications requires trial_index or cockpit_playback_limit')
        if fields & {'trial_index'} and (
                message.get('loss_notifications') is not True or type(trial_index) is not int
                or not 1 <= trial_index <= 200000 or trial_index == grant.index):
            raise ValueError('invalid movement trial index')
        if fields & {'cockpit_playback_limit'} and (
                message.get('loss_notifications') is not True or type(cockpit_limit) is not int
                or not 1 <= cockpit_limit <= MAX_COCKPIT_PLAYBACK_PILOTS):
            raise ValueError('invalid cockpit playback limit')
        if 'pilot_roster' in fields and (message['pilot_roster'] is not True or cockpit_limit is None):
            raise ValueError('pilot_roster requires cockpit playback')

        route = None
        if 'resume_route' in fields:
            claim = message['resume_route']
            if (trial_index is None or type(claim) is not list or len(claim) != 3
                    or type(claim[0]) is not int or claim[0] != trial_index
                    or type(claim[1]) is not str or not _HEX32.fullmatch(claim[1])
                    or type(claim[2]) is not str or not SECTOR.fullmatch(claim[2])):
                raise ValueError('invalid movement resume route')
            if self.running and tuple(claim) == grant.trial_route:
                route = grant.trial_route
        resumed_routes = {}
        if 'resume_routes' in fields:
            claims = message['resume_routes']
            if cockpit_limit is None or type(claims) is not list or len(claims) > cockpit_limit:
                raise ValueError('invalid cockpit resume routes')
            seen_indices = set()
            for claim in claims:
                if (type(claim) is not list or len(claim) != 3
                        or type(claim[0]) is not int or not 1 <= claim[0] <= 200000
                        or claim[0] == grant.index or claim[0] in seen_indices
                        or type(claim[1]) is not str or not _HEX32.fullmatch(claim[1])
                        or type(claim[2]) is not str or not SECTOR.fullmatch(claim[2])):
                    raise ValueError('invalid cockpit resume route entry')
                seen_indices.add(claim[0])
                triple = tuple(claim)
                if self.running and grant.cockpit_routes.get(claim[0]) == triple:
                    resumed_routes[claim[0]] = triple
        if MEASUREMENT_KIND in fields:
            if message[MEASUREMENT_KIND] is not True or self._npc_measurement is None:
                raise ValueError('private NPC measurement unavailable')
            self._npc_measurement.bind(peer, grant)
        grant.trial_route = peer.resuming = route
        grant.cockpit_routes = dict(resumed_routes)
        peer.resuming_routes = dict(resumed_routes)
        peer.grant = grant
        peer.trial_index = trial_index
        peer.cockpit_playback_limit = cockpit_limit or 0
        peer.pilot_roster = message.get('pilot_roster', False)
        grant.traffic.channel_bindings += 1
        bound = {'kind': 'motion_bound', 'window': self.window, 'running': self.running,
                 'last_sequence': grant.last_sequence}
        if trial_index is not None:
            bound.update(loss_notifications=True, trial_index=trial_index)
        if 'resume_route' in fields:
            bound['resume_valid'] = route is not None
        if cockpit_limit is not None:
            bound.update(loss_notifications=True, cockpit_playback_limit=cockpit_limit)
        if 'resume_routes' in fields:
            bound['resume_valid_indices'] = sorted(resumed_routes)
        if peer.pilot_roster:
            bound['pilot_roster'] = True
        if MEASUREMENT_KIND in fields:
            bound[MEASUREMENT_KIND] = True
        self._notice(peer, bound)

    def _view(self, peer, message):
        fields = {'kind', 'window', 'view_id'}
        if message['kind'] == 'motion_view' and set(message) == fields | {'sector_macro', 'indices'}:
            rows = [{'sector_macro': message['sector_macro'], 'indices': message['indices']}]
        elif message['kind'] == 'motion_views' and set(message) == fields | {'views'}:
            rows = message['views']
        else:
            raise ValueError('invalid observed view')
        if type(message['view_id']) is not int or not peer.view_id < message['view_id'] <= MAX_COUNTER:
            raise ValueError('invalid observed view revision')
        views = normalize_views(rows, own_index=peer.grant.index)
        peer.grant.traffic.view_updates += 1
        if views:
            peer.grant.traffic.nonempty_view_updates += 1
        previous = peer.views
        peer.view_id = message['view_id']
        peer.views = views
        if self._npc_measurement is not None:
            self._npc_measurement.view(peer, self.window, time.monotonic())
        peer.delivered = {index: route for index, route in peer.delivered.items()
                          if view_matches(views, index, route[1])}
        peer.hidden.intersection_update(peer.delivered)
        if peer.resuming is not None:
            index, generation, sector = peer.resuming
            peer.resuming = None
            if view_matches(views, index, sector):
                if (index, generation, sector) == peer.grant.trial_route:
                    peer.delivered[index] = generation, sector
                else:
                    # Authority may have ended after bind, before the view was restored.
                    self._notice(peer, {'kind': 'motion_end', 'window': self.window, 'view_id': peer.view_id,
                                        'index': index, 'generation': generation})
        if peer.resuming_routes:
            resuming_routes, peer.resuming_routes = peer.resuming_routes, {}
            for index, (r_index, generation, sector) in resuming_routes.items():
                if view_matches(views, r_index, sector):
                    if peer.grant.cockpit_routes.get(r_index) == (r_index, generation, sector):
                        peer.delivered[r_index] = generation, sector
                    else:
                        self._notice(peer, {'kind': 'motion_end', 'window': self.window, 'view_id': peer.view_id,
                                            'index': r_index, 'generation': generation})
        route = peer.grant.trial_route
        if route is not None and not view_matches(views, route[0], route[2]):
            peer.grant.trial_route = None
        for r_index, croute in tuple(peer.grant.cockpit_routes.items()):
            if not view_matches(views, croute[0], croute[2]):
                del peer.grant.cockpit_routes[r_index]
        peer.latest = {index: sample for index, sample in peer.latest.items()
                       if view_matches(views, index, sample['sector_macro'])}
        now = time.monotonic()
        for source in (*self._grants.values(), self._npc_observation):
            if source is None or source.last_sample is None or source.visible_until <= now:
                continue
            sample = source.last_sample
            index = sample['index']
            sector = sample['sector_macro']
            if (sample['epoch'] == peer.grant.epoch and view_matches(views, index, sector)
                    and not view_matches(previous, index, sector)):
                peer.latest[index] = sample

    def _fan_out(self, sample, *, source_peer=None):
        index, sector, epoch = sample['index'], sample['sector_macro'], sample['epoch']
        for viewer in self._peers.values():
            if (viewer is not source_peer and viewer.grant is not None and viewer.grant.epoch == epoch
                    and view_matches(viewer.views, index, sector)):
                viewer.latest[index] = sample

    def _sample(self, peer, message):
        if set(message) != {'kind', 'window', 'sample'}:
            raise ValueError('invalid participant snapshot')
        value = motion_value(message['sample'])
        grant = peer.grant
        if (self._grants.get(grant.handle) is not grant or
                (value['version'], value['index'], value['epoch'], value['generation']) !=
                (3, grant.index, grant.epoch, grant.generation) or value['sequence'] <= grant.last_sequence):
            raise ValueError('snapshot does not match this ship grant')
        if grant.last_sample is None and value['real_velocity'] is not None:
            raise ValueError('initial sample must establish a new motion baseline')
        check_motion_progress(value, grant.last_accepted)
        if grant.last_accepted is not None and grant.last_accepted['sector_macro'] != value['sector_macro']:
            self._retire_sample(grant.index, grant.generation)
            grant.sector_revision += 1
            # An inspected pose stops authorizing writes immediately. Removing
            # that entry also keeps it invalid if the grant is later revoked.
            self._application_batch.pop(grant.index, None)
        now = time.monotonic()
        grant.traffic.record_source(value, grant.last_sample if grant.visible_until > now else None)
        grant.last_accepted = grant.last_sample = value
        grant.last_sequence = value['sequence']
        grant.visible_until = now + MAX_SAMPLE_AGE
        self._remember_application(AcceptedCompanyMotion(
            grant.handle, self.window, now, value, sector_revision=grant.sector_revision))
        self.accepted_samples += 1
        self.samples_with_velocity += int(value['real_velocity'] is not None)
        self.last_ship_position = {key: value[key] for key in ('index', 'sector_macro', 'sequence')}
        peer.source_ack = encode_frame({'kind': 'motion_sampled', 'window': self.window,
                                   'sequence': grant.last_sequence})
        self._fan_out(value, source_peer=peer)

    def _receive(self, peer, message):
        if peer.grant is None:
            self._bind(peer, message)
            return
        window = message.get('window')
        if type(window) is not int or not 0 <= window <= self.window:
            raise ValueError('invalid movement window')
        if message.get('kind') == MEASUREMENT_KIND:
            if self._npc_measurement is None or window != self.window or not self.running:
                raise ValueError('private NPC measurement outside active window')
            self._npc_measurement.receive(peer, message, window, time.monotonic())
            return
        if window < self.window or not self.running:
            return
        if message.get('kind') == 'motion_received':
            fields = ('view_id', 'index', 'generation', 'sequence')
            if (set(message) != {'kind', 'window', *fields} or
                    any(type(message[key]) is not int for key in ('view_id', 'index', 'sequence')) or
                    tuple(message[key] for key in fields) != peer.awaiting_ack):
                raise ValueError('movement receipt does not match the in-flight snapshot')
            peer.awaiting_ack = None
            peer.grant.traffic.snapshot_receipts += 1
            peer.grant.traffic.last_received_ship_index = message['index']
        elif message.get('kind') in ('motion_view', 'motion_views'):
            self._view(peer, message)
        elif message.get('kind') == 'motion_sample':
            self._sample(peer, message)
        else:
            raise ValueError('unknown movement message')

    def _write(self, peer):
        roster = None
        if peer.pilot_roster and self.running:
            # The bounded control grants are authoritative across movement socket loss.
            # Diagnostic services can admit more ships than the regular company session.
            # Preserve a stable subset of the lowest same-epoch indices in that case.
            indices = sorted(grant.index for grant in self._grants.values()
                             if grant is not peer.grant and grant.epoch == peer.grant.epoch)
            roster = tuple(indices[:MAX_COCKPIT_PLAYBACK_PILOTS])
        for _ in range(8):
            sending_roster = False
            if not peer.outgoing:
                if peer.notices:
                    peer.outgoing = peer.notices.popleft()
                elif peer.source_ack:
                    peer.outgoing, peer.source_ack = peer.source_ack, b''
                elif roster is not None and roster != peer.last_pilot_roster:
                    sending_roster = True
                    peer.outgoing = encode_frame({'kind': 'pilot_roster', 'window': self.window,
                                                  'epoch': peer.grant.epoch, 'ship_indices': roster})
                elif peer.latest and peer.awaiting_ack is None:
                    index = next(iter(peer.latest))
                    sample = peer.latest.pop(index)
                    peer.delivered[index] = sample['generation'], sample['sector_macro']
                    peer.hidden.discard(index)
                    if index == peer.trial_index:
                        peer.grant.trial_route = (index, sample['generation'], sample['sector_macro'])
                    if peer.cockpit_playback_limit > 0:
                        peer.grant.cockpit_routes[index] = (index, sample['generation'], sample['sector_macro'])
                    peer.awaiting_ack = (peer.view_id, index, sample['generation'], sample['sequence'])
                    peer.outgoing = encode_frame({'kind': 'motion_snapshot', 'window': self.window,
                                             'view_id': peer.view_id, 'sample': sample})
                else:
                    return
            try:
                sent = peer.connection.send(peer.outgoing)
            except BlockingIOError:
                if sending_roster:
                    # Retry with the current membership if no bytes entered the stream.
                    peer.outgoing = b''
                return
            if not sent:
                raise ConnectionError('movement send closed')
            if sending_roster:
                peer.last_pilot_roster = roster
            peer.outgoing = peer.outgoing[sent:]

    def poll(self):
        """Bound ingress and egress work per peer so world control can progress."""
        self._check_owner()
        now = time.monotonic()
        if self._npc_measurement is not None:
            self._npc_measurement.check(now)
        observation = self._current_npc()
        if observation is not None and observation.visible_until and observation.visible_until <= now:
            observation.visible_until = 0
            self._retire_sample(observation.binding.index, observation.binding.generation)
        for grant in self._grants.values():
            if grant.visible_until and grant.visible_until <= now:
                grant.visible_until = 0
                self._retire_sample(grant.index, grant.generation, transport_lost=True)
        for _ in range(MAX_PEERS):
            try:
                connection, _ = self.listener.accept()
            except BlockingIOError:
                break
            if len(self._peers) >= MAX_PEERS:
                connection.close()
                continue
            connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            connection.setblocking(False)
            self._peers[connection] = _Peer(connection, now + 10)
        for peer in tuple(self._peers.values()):
            if peer.connection not in self._peers:
                continue
            try:
                if peer.grant is None and now >= peer.bind_deadline:
                    raise TimeoutError('movement bind expired')
                for message in read_frames(peer.connection, peer.incoming):
                    self._receive(peer, message)
                self._write(peer)
            except OSError:
                self._drop(peer)
            except (ValueError, RecursionError):
                self._drop(peer, transport_lost=False)

    def close(self):
        self._check_owner()
        self.listener.close()
        for peer in tuple(self._peers.values()):
            self._drop(peer)
        self._grants.clear()
        self._tickets.clear()
        self._application_pending.clear()
        self._application_batch.clear()
        self._npc_observation = None
