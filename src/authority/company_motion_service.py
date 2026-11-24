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
from .company_motion_frames import (MAX_PEERS, MAX_COUNTER, SECTOR, encode_frame,
                                    normalize_views, read_frames, view_matches)

_HEX32 = re.compile(r'[0-9a-f]{32}')
_TICKET = re.compile(r'[0-9a-f]{64}')
# A stopped game sampler cannot refresh a new viewer from an indefinitely old pose.
MAX_SAMPLE_AGE = 3.0


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
        self._owner = threading.current_thread()
        self._application_pending = {}
        self._application_batch = {}
        self.running = False
        self.window = 0
        self.accepted_samples = 0
        self.samples_with_velocity = 0
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
        grant = _Grant(handle, secrets.token_hex(32), ship_index, registry_epoch, secrets.token_hex(16))
        # A previous grant's sealed sample cannot move a reassigned ship.
        self._application_pending.pop(ship_index, None)
        self._application_batch.pop(ship_index, None)
        self._grants[handle] = self._tickets[grant.ticket] = grant
        return {'ticket': grant.ticket, 'epoch': grant.epoch, 'generation': grant.generation,
                'index': ship_index, 'session_epoch': session_epoch}

    def _drop(self, peer):
        if self._peers.pop(peer.connection, None) is None:
            return
        peer.connection.close()
        grant = peer.grant
        if grant is not None and self._grants.get(grant.handle) is grant and grant.last_sample is not None:
            self._retire_sample(grant)
            grant.last_sample = None

    def _notice(self, peer, message):
        if len(peer.notices) >= 2 * MAX_PEERS:
            self._drop(peer)
        else:
            peer.notices.append(encode_frame(message))

    def _retire_sample(self, grant):
        for viewer in tuple(self._peers.values()):
            viewer.latest.pop(grant.index, None)
            if grant.index in viewer.delivered:
                del viewer.delivered[grant.index]
                self._notice(viewer, {'kind': 'motion_end', 'window': self.window, 'view_id': viewer.view_id,
                                      'index': grant.index, 'generation': grant.generation})

    def revoke(self, handle):
        self._check_owner()
        grant = self._grants.pop(handle, None)
        if grant is None:
            return
        self._tickets.pop(grant.ticket)
        for peer in tuple(self._peers.values()):
            if peer.grant is grant:
                self._drop(peer)
        self._retire_sample(grant)

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
        for peer in tuple(self._peers.values()):
            if peer.grant is None:
                continue
            peer.latest.clear()
            peer.delivered.clear()
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
        if (set(message) != {'kind', 'ticket'} or message['kind'] != 'bind_motion' or
                type(ticket) is not str or not _TICKET.fullmatch(ticket)):
            raise ValueError('invalid movement binding')
        grant = self._tickets.get(ticket)
        if grant is None or any(other.grant is grant for other in self._peers.values()):
            raise ValueError('movement ticket unavailable')
        peer.grant = grant
        self._notice(peer, {'kind': 'motion_bound', 'window': self.window, 'running': self.running,
                            'last_sequence': grant.last_sequence})

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
        peer.delivered = {index: sector for index, sector in peer.delivered.items()
                          if view_matches(views, index, sector)}
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
        if grant.last_sample is not None and grant.last_sample['sector_macro'] != value['sector_macro']:
            self._retire_sample(grant)
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
                    peer.delivered[index] = sample['sector_macro']
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
                self._retire_sample(grant)
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
            except (ValueError, OSError, RecursionError):
                self._drop(peer)

    def close(self):
        self._check_owner()
        self.listener.close()
        for peer in tuple(self._peers.values()):
            self._drop(peer)
        self._grants.clear()
        self._tickets.clear()
        self._application_pending.clear()
        self._application_batch.clear()
