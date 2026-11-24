"""Local backend for the pinned TLS movement service, polled by the world owner."""
from collections import deque
from dataclasses import dataclass, field
import re
import secrets
import socket
import time

from .experimental_tcp_motion import check_motion_progress, motion_value
from .company_motion_frames import MAX_PEERS, MAX_COUNTER, SECTOR, encode_frame, read_frames, validate_view

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
    delivered: set = field(default_factory=set)
    awaiting_ack: tuple | None = None
    source_ack: bytes = b''
    view_id: int = 0
    sector: str | None = None
    indices: set = field(default_factory=set)


class CompanyMotionService:
    """Authorize a stream for one admitted ship and coalesce unsent views by ship.

    All methods run on the supervisor thread. The TLS gateway on the same public
    port connects only to this loopback listener. Control-session cleanup revokes
    its grant; a movement socket closure cannot remove a player from the world.
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
        self.running = False
        self.window = 0
        self.accepted_samples = 0
        self.samples_with_velocity = 0
        self.last_ship_position = None

    def admit(self, handle, attempt_id, ship_index, sector_macro, session_epoch, registry_epoch):
        if (type(handle) is not int or handle < 1 or handle in self._grants or
                type(ship_index) is not int or not 1 <= ship_index <= 200000 or
                any(not isinstance(token, str) or not _HEX32.fullmatch(token)
                    for token in (attempt_id, session_epoch, registry_epoch)) or
                not isinstance(sector_macro, str) or not SECTOR.fullmatch(sector_macro) or
                any(grant.index == ship_index for grant in self._grants.values())):
            raise ValueError('invalid participant movement grant')
        grant = _Grant(handle, secrets.token_hex(32), ship_index, registry_epoch, secrets.token_hex(16))
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
                viewer.delivered.discard(grant.index)
                self._notice(viewer, {'kind': 'motion_end', 'window': self.window, 'view_id': viewer.view_id,
                                      'index': grant.index, 'generation': grant.generation})

    def revoke(self, handle):
        grant = self._grants.pop(handle, None)
        if grant is None:
            return
        self._tickets.pop(grant.ticket)
        for peer in tuple(self._peers.values()):
            if peer.grant is grant:
                self._drop(peer)
        self._retire_sample(grant)

    def set_running(self, running):
        if type(running) is not bool:
            raise ValueError('invalid movement clock state')
        if self.running == running:
            return
        self.running = running
        self.window += 1
        for grant in self._grants.values():
            grant.last_sample = None
            grant.visible_until = 0
        for peer in tuple(self._peers.values()):
            if peer.grant is None:
                continue
            peer.latest.clear()
            peer.delivered.clear()
            peer.sector, peer.indices = None, set()
            peer.awaiting_ack, peer.source_ack, peer.view_id = None, b'', 0
            self._notice(peer, {'kind': 'motion_state', 'window': self.window, 'running': running})

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
        indices, sector = message.get('indices'), message.get('sector_macro')
        if (set(message) != {'kind', 'window', 'view_id', 'sector_macro', 'indices'} or
                type(message['view_id']) is not int or not peer.view_id < message['view_id'] <= MAX_COUNTER):
            raise ValueError('invalid observed view revision')
        validate_view(sector, indices)
        peer.delivered.clear()
        peer.view_id = message['view_id']
        peer.sector, peer.indices = sector, set(indices) - {peer.grant.index}
        peer.latest.clear()
        for grant in self._grants.values():
            sample = grant.last_sample
            if (grant.index in peer.indices and sample is not None and grant.visible_until > time.monotonic()
                    and sample['sector_macro'] == sector):
                peer.latest[grant.index] = sample

    def _sample(self, peer, message):
        if set(message) != {'kind', 'window', 'sample'}:
            raise ValueError('invalid participant snapshot')
        value = motion_value(message['sample'])
        grant = peer.grant
        if ((value['version'], value['index'], value['epoch'], value['generation']) !=
                (3, grant.index, grant.epoch, grant.generation) or value['sequence'] <= grant.last_sequence):
            raise ValueError('snapshot does not match this ship grant')
        if grant.last_sample is None and value['real_velocity'] is not None:
            raise ValueError('initial sample must establish a new motion baseline')
        check_motion_progress(value, grant.last_sample)
        if grant.last_sample is not None and grant.last_sample['sector_macro'] != value['sector_macro']:
            self._retire_sample(grant)
        grant.last_sample, grant.last_sequence = value, value['sequence']
        grant.visible_until = time.monotonic() + MAX_SAMPLE_AGE
        self.accepted_samples += 1
        self.samples_with_velocity += int(value['real_velocity'] is not None)
        self.last_ship_position = {key: value[key] for key in ('index', 'sector_macro', 'sequence')}
        peer.source_ack = encode_frame({'kind': 'motion_sampled', 'window': self.window,
                                   'sequence': grant.last_sequence})
        for viewer in self._peers.values():
            if viewer is not peer and grant.index in viewer.indices and viewer.sector == value['sector_macro']:
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
        elif message.get('kind') == 'motion_view':
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
                    peer.delivered.add(index)
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
        self.listener.close()
        for peer in tuple(self._peers.values()):
            self._drop(peer)
        self._grants.clear()
        self._tickets.clear()
