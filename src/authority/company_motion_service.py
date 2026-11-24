"""Local backend for the pinned TLS movement service, polled by the world owner."""
from collections import deque
import copy
from dataclasses import dataclass, field
import re
import secrets
import socket
import threading
import time

from .experimental_tcp_motion import check_motion_progress, motion_value
from .company_motion_frames import (MAX_INDICES, MAX_PEERS, MAX_COUNTER, SECTOR, encode_frame,
                                    normalize_views, read_frames, view_matches)

_HEX32 = re.compile(r'[0-9a-f]{32}')
_TICKET = re.compile(r'[0-9a-f]{64}')
# A stopped game sampler cannot refresh a new viewer from an indefinitely old pose.
MAX_SAMPLE_AGE = 3.0
MAX_NPC_TRACKS = 1024
MAX_NOTICES = max(2 * MAX_PEERS, 2 * MAX_INDICES)


@dataclass
class _NpcTrack:
    index: int
    epoch: str
    generation: str
    last_sample: dict | None = None
    last_accepted: dict | None = None
    last_sequence: int = 0
    visible_until: float = 0


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
    visible_until: float = 0
    trial_route: tuple | None = None


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
    resuming: tuple | None = None
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

    @property
    def index(self):
        return self._sample['index']

    @property
    def sample(self):
        return copy.deepcopy(self._sample)


class CompanyMotionService:
    """Authorize a stream for one admitted ship and coalesce unsent views by ship.

    All methods run on the supervisor thread. The TLS gateway on the same public
    port connects only to this loopback listener. Control-session cleanup revokes
    its grant; a movement socket closure cannot remove a player from the world.
    A source acknowledgement confirms transport acceptance. The world owner
    separately takes and settles samples before any native pause and save.
    """
    def __init__(self):
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
        self._tickets = {}
        self._peers = {}
        self._npc_tracks = {}
        self._owner = threading.current_thread()
        self._application_pending = {}
        self._application_batch = {}
        self.running = False
        self.window = 0
        self.accepted_samples = 0
        self.samples_with_velocity = 0
        self.accepted_npc_samples = 0
        self.npc_samples_with_velocity = 0
        self.last_ship_position = None

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
        self.retire_npc_track(ship_index)
        self._retire_sample(ship_index)
        grant = _Grant(handle, secrets.token_hex(32), ship_index, registry_epoch, secrets.token_hex(16))
        # A previous grant's sealed sample cannot move a reassigned ship.
        self._application_pending.pop(ship_index, None)
        self._application_batch.pop(ship_index, None)
        self._grants[handle] = self._tickets[grant.ticket] = grant
        return {'ticket': grant.ticket, 'epoch': grant.epoch, 'generation': grant.generation,
                'index': ship_index, 'session_epoch': session_epoch}

    def _drop(self, peer, *, transport_lost=True):
        if self._peers.pop(peer.connection, None) is None:
            return
        peer.connection.close()
        grant = peer.grant
        if (grant is not None and self._grants.get(grant.handle) is grant
                and (grant.last_sample is not None or not transport_lost)):
            if not transport_lost:
                grant.trial_route = None
            self._retire_sample(grant.index, grant.generation, transport_lost=transport_lost)
            grant.last_sample = None

    def _notice(self, peer, message):
        if len(peer.notices) >= MAX_NOTICES:
            self._drop(peer, transport_lost=False)
        else:
            peer.notices.append(encode_frame(message))

    def _retire_sample(self, index, generation=None, *, transport_lost=False):
        if not transport_lost:
            for viewer_grant in self._grants.values():
                route = viewer_grant.trial_route
                if route is not None and route[0] == index and (generation is None or route[1] == generation):
                    viewer_grant.trial_route = None
        for viewer in tuple(self._peers.values()):
            viewer.latest.pop(index, None)
            delivered = viewer.delivered.get(index)
            if delivered is None or (generation is not None and delivered[0] != generation):
                continue
            kind = 'motion_end'
            if transport_lost and viewer.trial_index == index:
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
        self._tickets.pop(grant.ticket)
        for peer in tuple(self._peers.values()):
            if peer.grant is grant:
                self._drop(peer)
        self._retire_sample(grant.index, grant.generation, transport_lost=transport_lost)

    def set_running(self, running):
        self._check_owner()
        if type(running) is not bool:
            raise ValueError('invalid movement clock state')
        if self.running == running:
            return
        self.running = running
        self.window += 1
        self._application_pending.clear()
        self._application_batch.clear()
        for grant in self._grants.values():
            grant.last_sample = None
            grant.visible_until = 0
            grant.trial_route = None
        for track in self._npc_tracks.values():
            track.last_sample = None
            track.visible_until = 0
        for peer in tuple(self._peers.values()):
            if peer.grant is None:
                continue
            peer.latest.clear()
            peer.delivered.clear()
            peer.hidden.clear()
            peer.resuming = None
            peer.views.clear()
            peer.awaiting_ack, peer.source_ack, peer.view_id = None, b'', 0
            self._notice(peer, {'kind': 'motion_state', 'window': self.window, 'running': running})

    def _check_owner(self):
        if threading.current_thread() is not self._owner:
            raise RuntimeError('exclusive company motion service owner required')

    @staticmethod
    def _fresh(record, now):
        return 0 <= now - record.accepted_at < MAX_SAMPLE_AGE

    def _remember_application(self, record):
        pending = self._application_pending
        if record.index not in pending and len(pending) >= MAX_PEERS:
            # Active grants themselves are bounded. Churn can fill the queue
            # with departed grants, so keep active samples ahead of older ones.
            active = {grant.index for grant in self._grants.values()}
            oldest_departed = next(index for index in pending if index not in active)
            del pending[oldest_departed]
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
                del pending[index]
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

    def is_application_current(self, record):
        """Check a record from the current running or frozen batch before dispatch."""
        self._check_owner()
        return (type(record) is AcceptedCompanyMotion and
                self._application_batch.get(record.index) is record and
                record.window == self.window - int(not self.running) and
                self._fresh(record, time.monotonic()))

    def _bind(self, peer, message):
        ticket = message.get('ticket')
        fields = set(message)
        if (fields - {'kind', 'ticket', 'loss_notifications', 'trial_index', 'resume_route'}
                or message.get('kind') != 'bind_motion'
                or type(ticket) is not str or not _TICKET.fullmatch(ticket)):
            raise ValueError('invalid movement binding')
        grant = self._tickets.get(ticket)
        if grant is None or any(other.grant is grant for other in self._peers.values()):
            raise ValueError('movement ticket unavailable')
        trial_index = message.get('trial_index')
        if fields & {'loss_notifications', 'trial_index'} and (
                message.get('loss_notifications') is not True or type(trial_index) is not int
                or not 1 <= trial_index <= 200000 or trial_index == grant.index):
            raise ValueError('invalid movement trial index')

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
        grant.trial_route = peer.resuming = route
        peer.grant = grant
        peer.trial_index = trial_index
        bound = {'kind': 'motion_bound', 'window': self.window, 'running': self.running,
                 'last_sequence': grant.last_sequence}
        if trial_index is not None:
            bound.update(loss_notifications=True, trial_index=trial_index)
        if 'resume_route' in fields:
            bound['resume_valid'] = route is not None
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
        previous = peer.views
        peer.view_id = message['view_id']
        peer.views = views
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
        route = peer.grant.trial_route
        if route is not None and not view_matches(views, route[0], route[2]):
            peer.grant.trial_route = None
        peer.latest = {index: sample for index, sample in peer.latest.items()
                       if view_matches(views, index, sample['sector_macro'])}
        now = time.monotonic()
        for grant in self._grants.values():
            sample = grant.last_sample
            if sample is None or grant.visible_until <= now:
                continue
            sector = sample['sector_macro']
            if view_matches(views, grant.index, sector) and not view_matches(previous, grant.index, sector):
                peer.latest[grant.index] = sample
        for track in self._npc_tracks.values():
            sample = track.last_sample
            if sample is None or track.visible_until <= now:
                continue
            sector = sample['sector_macro']
            if view_matches(views, track.index, sector) and not view_matches(previous, track.index, sector):
                peer.latest[track.index] = sample

    def subscribed_npc_targets(self):
        """Return {sector_macro: frozenset(indices)} subscribed across admitted observers, excluding player grants."""
        self._check_owner()
        player_indices = {grant.index for grant in self._grants.values()}
        merged = {}
        for peer in self._peers.values():
            if peer.grant is None:
                continue
            for sector, indices in peer.views.items():
                npc_indices = indices - player_indices
                if npc_indices:
                    merged.setdefault(sector, set()).update(npc_indices)
        return {sector: frozenset(indices) for sector, indices in merged.items()}

    def publish_npc_sample(self, raw_sample):
        """Publish one authoritative server-side V3 NPC sample to matching admitted observers."""
        self._check_owner()
        value = motion_value(raw_sample)
        index = value['index']
        if value['version'] != 3 or not 1 <= index <= 200000:
            raise ValueError('authoritative NPC sample must be version 3 within registry bounds')
        if any(grant.index == index for grant in self._grants.values()):
            raise ValueError('ship index owned by an admitted player grant')
        if not self.running:
            return False
        now = time.monotonic()
        track = self._npc_tracks.get(index)
        if track is None or track.generation != value['generation']:
            if value['real_velocity'] is not None:
                raise ValueError('initial NPC sample must establish a new motion baseline')
            if track is not None:
                self._retire_sample(index, track.generation, transport_lost=False)
            elif len(self._npc_tracks) >= MAX_NPC_TRACKS:
                expired = [idx for idx, item in self._npc_tracks.items()
                           if item.visible_until <= now]
                for idx in expired:
                    old = self._npc_tracks.pop(idx)
                    if old.last_sample is not None:
                        self._retire_sample(idx, old.generation, transport_lost=True)
                if len(self._npc_tracks) >= MAX_NPC_TRACKS:
                    raise ValueError('authoritative NPC track capacity reached')
            track = _NpcTrack(index=index, epoch=value['epoch'], generation=value['generation'])
            self._npc_tracks[index] = track
        else:
            if value['epoch'] != track.epoch or value['sequence'] <= track.last_sequence:
                raise ValueError('stale or foreign authoritative NPC sample')
            if track.last_sample is None and value['real_velocity'] is not None:
                raise ValueError('resumed NPC sample must establish a new motion baseline')
            check_motion_progress(value, track.last_accepted)
            if track.last_accepted is not None and track.last_accepted['sector_macro'] != value['sector_macro']:
                self._retire_sample(index, track.generation, transport_lost=False)
        track.last_accepted = track.last_sample = value
        track.last_sequence = value['sequence']
        track.visible_until = now + MAX_SAMPLE_AGE
        self.accepted_npc_samples += 1
        self.npc_samples_with_velocity += int(value['real_velocity'] is not None)
        for viewer in self._peers.values():
            if viewer.grant is not None and view_matches(viewer.views, index, value['sector_macro']):
                viewer.latest[index] = value
        return True

    def retire_npc_track(self, index, generation=None, *, transport_lost=False):
        """Retire one authoritative NPC track and notify subscribed observers."""
        self._check_owner()
        track = self._npc_tracks.get(index)
        if track is None:
            return False
        if generation is not None and track.generation != generation:
            return False
        self._npc_tracks.pop(index)
        self._retire_sample(index, track.generation, transport_lost=transport_lost)
        return True

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
        now = time.monotonic()
        grant.last_accepted = grant.last_sample = value
        grant.last_sequence = value['sequence']
        grant.visible_until = now + MAX_SAMPLE_AGE
        self._remember_application(AcceptedCompanyMotion(grant.handle, self.window, now, value))
        self.accepted_samples += 1
        self.samples_with_velocity += int(value['real_velocity'] is not None)
        self.last_ship_position = {key: value[key] for key in ('index', 'sector_macro', 'sequence')}
        peer.source_ack = encode_frame({'kind': 'motion_sampled', 'window': self.window,
                                   'sequence': grant.last_sequence})
        for viewer in self._peers.values():
            if viewer is not peer and view_matches(viewer.views, grant.index, value['sector_macro']):
                viewer.latest[grant.index] = value

    def _receive(self, peer, message):
        if peer.grant is None:
            self._bind(peer, message)
            return
        window = message.get('window')
        if type(window) is not int or not 0 <= window <= self.window:
            raise ValueError('invalid movement window')
        if window < self.window or not self.running:
            return
        if message.get('kind') == 'motion_received':
            fields = ('view_id', 'index', 'generation', 'sequence')
            if (set(message) != {'kind', 'window', *fields} or
                    any(type(message[key]) is not int for key in ('view_id', 'index', 'sequence')) or
                    tuple(message[key] for key in fields) != peer.awaiting_ack):
                raise ValueError('movement receipt does not match the in-flight snapshot')
            peer.awaiting_ack = None
        elif message.get('kind') in ('motion_view', 'motion_views'):
            self._view(peer, message)
        elif message.get('kind') == 'motion_sample':
            self._sample(peer, message)
        else:
            raise ValueError('unknown movement message')

    def _write(self, peer):
        for _ in range(8):
            if not peer.outgoing:
                if peer.notices:
                    peer.outgoing = peer.notices.popleft()
                elif peer.source_ack:
                    peer.outgoing, peer.source_ack = peer.source_ack, b''
                elif peer.latest and peer.awaiting_ack is None:
                    index = next(iter(peer.latest))
                    sample = peer.latest.pop(index)
                    peer.delivered[index] = sample['generation'], sample['sector_macro']
                    peer.hidden.discard(index)
                    if index == peer.trial_index:
                        peer.grant.trial_route = (index, sample['generation'], sample['sector_macro'])
                    peer.awaiting_ack = (peer.view_id, index, sample['generation'], sample['sequence'])
                    peer.outgoing = encode_frame({'kind': 'motion_snapshot', 'window': self.window,
                                             'view_id': peer.view_id, 'sample': sample})
                else:
                    return
            try:
                sent = peer.connection.send(peer.outgoing)
            except BlockingIOError:
                return
            if not sent:
                raise ConnectionError('movement send closed')
            peer.outgoing = peer.outgoing[sent:]

    def poll(self):
        """Bound ingress and egress work per peer so world control can progress."""
        self._check_owner()
        now = time.monotonic()
        for grant in self._grants.values():
            if grant.visible_until and grant.visible_until <= now:
                grant.visible_until = 0
                self._retire_sample(grant.index, grant.generation, transport_lost=True)
        for track in self._npc_tracks.values():
            if track.visible_until and track.visible_until <= now:
                track.visible_until = 0
                track.last_sample = None
                self._retire_sample(track.index, track.generation, transport_lost=True)
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
        self._npc_tracks.clear()
        self._application_pending.clear()
        self._application_batch.clear()
