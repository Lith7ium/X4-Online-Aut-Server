"""Publish the selected cockpit and receive explicitly observed ships over the TLS bridge."""
import socket
import time

from .company_motion_frames import MAX_COUNTER, encode_frame, read_frames, validate_view
from .experimental_tcp_motion import motion_value
from .trusted_load_messages import validate_ship_motion_credential


class CompanyMotionClient:
    """Poll on the player owner thread. Only one sample awaits a receipt in each direction."""

    def __init__(self, port, credential, source, validate_peer, *, on_snapshot=None, on_end=None):
        epoch, index = source.registry
        if type(credential) is not dict or not callable(validate_peer):
            raise ValueError('movement admission and local source validator required')
        credential = validate_ship_motion_credential(credential, index=index, registry_epoch=epoch,
                                                     session_epoch=credential.get('session_epoch'))
        if not (type(port) is int and 1 <= port <= 65535):
            raise ValueError('local movement bridge required')
        self._connection = socket.create_connection(('127.0.0.1', port), timeout=2)
        try:
            self._connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._connection.setblocking(False)
            self._outgoing = encode_frame({'kind': 'bind_motion', 'ticket': credential['ticket']})
        except BaseException:
            self._connection.close()
            raise
        self._source = source
        self._validate_peer = validate_peer
        self._on_snapshot = on_snapshot
        self._on_end = on_end
        self._generation = credential['generation']
        self._incoming = bytearray()
        self._bind_deadline = time.monotonic() + 15
        self._window = None
        self._running = False
        self._last_sequence = 0
        self._last_sector = None
        self._awaiting = self._latest = self._receipt = None
        self._desired_view = self._sent_view = None
        self._view_id = 0
        self._stats = dict(samples_acknowledged=0, velocity_samples_acknowledged=0,
                           snapshots_received=0, visibility_ends=0)

    def stats(self):
        return dict(self._stats)

    def set_view(self, sector, indices):
        """Replace the local observation set. Callers own game visibility and rendering."""
        validate_view(sector, indices)
        if not callable(self._on_snapshot) or not callable(self._on_end):
            raise ValueError('an observed view needs snapshot and visibility callbacks')
        self._desired_view = (sector, frozenset(indices) - {self._source.registry[1]})

    def _clock(self, message, binding):
        fields = {'kind', 'window', 'running'}
        if binding:
            fields.add('last_sequence')
        window = message.get('window')
        if (set(message) != fields or type(window) is not int or not 0 <= window <= MAX_COUNTER or
                type(message['running']) is not bool or binding != (self._window is None)):
            raise ValueError('invalid movement clock receipt')
        if binding:
            sequence = message['last_sequence']
            if type(sequence) is not int or not 0 <= sequence <= MAX_COUNTER:
                raise ValueError('invalid movement bind sequence')
            self._last_sequence = sequence
        elif window != self._window + 1 or message['running'] == self._running:
            raise ValueError('movement clock transition out of order')
        self._window, self._running = window, message['running']
        self._latest = self._awaiting = self._receipt = self._last_sector = None
        self._sent_view = None
        self._view_id = 0

    def _receive(self, message):
        kind = message.get('kind')
        if kind in ('motion_bound', 'motion_state'):
            self._clock(message, kind == 'motion_bound')
            return
        window = message.get('window')
        if self._window is None or type(window) is not int or window != self._window or not self._running:
            raise ValueError('received movement outside the running window')
        if kind == 'motion_sampled':
            sequence = message.get('sequence')
            if (set(message) != {'kind', 'sequence', 'window'} or type(sequence) is not int or
                    self._awaiting is None or sequence != self._awaiting[0]):
                raise ValueError('unexpected source receipt')
            self._stats['samples_acknowledged'] += 1
            self._stats['velocity_samples_acknowledged'] += int(self._awaiting[1])
            self._awaiting = None
            return
        view_id = message.get('view_id')
        if type(view_id) is not int or not 1 <= view_id <= self._view_id:
            raise ValueError('unexpected movement view')
        current_view = view_id == self._view_id and self._sent_view == self._desired_view
        if kind == 'motion_snapshot' and set(message) == {'kind', 'window', 'view_id', 'sample'}:
            sample = motion_value(message['sample'])
            if sample['version'] != 3 or sample['epoch'] != self._source.registry[0] or self._receipt is not None:
                raise ValueError('invalid remote movement snapshot')
            if current_view:
                sector, indices = self._sent_view
                if sample['index'] not in indices or sample['sector_macro'] != sector:
                    raise ValueError('remote ship outside selected view')
                self._on_snapshot(dict(sample))
                self._stats['snapshots_received'] += 1
            self._receipt = {'kind': 'motion_received', 'view_id': view_id, 'index': sample['index'],
                             'sequence': sample['sequence'], 'generation': sample['generation']}
        elif kind == 'motion_end' and set(message) == {'kind', 'window', 'view_id', 'index', 'generation'}:
            if current_view:
                if type(message['index']) is not int or message['index'] not in self._sent_view[1]:
                    raise ValueError('visibility end outside selected view')
                self._on_end(message['index'], message['generation'])
                self._stats['visibility_ends'] += 1
        else:
            raise ValueError('unknown remote movement frame')

    def _next_frame(self):
        if not self._running:
            return None
        if self._receipt is not None:
            frame, self._receipt = self._receipt, None
            return frame
        if self._sent_view != self._desired_view:
            self._sent_view = self._desired_view
            self._view_id += 1
            sector, indices = self._sent_view
            return {'kind': 'motion_view', 'view_id': self._view_id,
                    'sector_macro': sector, 'indices': sorted(indices)}
        if self._awaiting is not None or self._latest is None:
            return None
        sample, self._latest = self._latest, None
        if sample['sector_macro'] != self._last_sector:
            sample['real_velocity'] = None
        self._last_sector, self._last_sequence = sample['sector_macro'], sample['sequence']
        self._awaiting = (self._last_sequence, sample['real_velocity'] is not None)
        return {'kind': 'motion_sample', 'sample': sample}

    def poll(self):
        try:
            if self._window is None and time.monotonic() > self._bind_deadline:
                raise TimeoutError('movement stream did not bind to admitted player')
            old_window = self._window
            for message in read_frames(self._connection, self._incoming):
                self._receive(message)
            sample = self._source.poll(self._validate_peer)
            if sample is not None and self._running and old_window == self._window:
                sample = motion_value(sample)
                if ((sample['version'], sample['epoch'], sample['index'], sample['generation']) !=
                        (3, *self._source.registry, self._source.local_generation)):
                    raise ValueError('source does not match the selected local cockpit')
                if sample['sequence'] > self._last_sequence:
                    self._latest = dict(sample, generation=self._generation)
            for _ in range(4):
                if not self._outgoing:
                    frame = self._next_frame()
                    if frame is None:
                        break
                    self._outgoing = encode_frame(dict(frame, window=self._window))
                try:
                    sent = self._connection.send(self._outgoing)
                except BlockingIOError:
                    break
                if not sent:
                    raise ConnectionError('movement stream closed on write')
                self._outgoing = self._outgoing[sent:]
        except BaseException:
            self.close()
            raise

    def close(self):
        self._outgoing = b''
        self._connection.close()
