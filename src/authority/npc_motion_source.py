"""Opt-in authority-side loopback UDP receiver for live registered-NPC motion samples.

Keeps registered non-player NPC sampling separate from Company player-ship sampling
(`company_motion_source.py`), validates peer ownership through the platform UDP peer
validator (e.g. `WindowsMotionPeer`), checks V3 sample monotonicity via
`check_motion_progress`, and feeds standard V3 motion values directly into
`CompanyMotionClient` -> `CompanyMotionService`.
"""
from copy import deepcopy
import re
import secrets
import select
import socket
import threading

from .experimental_tcp_motion import check_motion_progress, motion_value

_MAX_NPC_SAMPLER_DATAGRAM = 512
_HEX32 = re.compile(r'[0-9a-f]{32}')


class NpcMotionSource:
    """Poll one authenticated live registered-NPC sampler on the owner thread."""

    def __init__(self, *, enabled=False, on_stage=None):
        if enabled is not True:
            raise ValueError('explicit opt-in required for NpcMotionSource')
        if on_stage is not None and not callable(on_stage):
            raise ValueError('invalid npc motion stage reporter')
        self._on_stage = on_stage
        self._reported_stages = set()
        self._thread = threading.get_ident()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            self._socket.bind(('127.0.0.1', 0))
            self._socket.setblocking(False)
            self._lease = secrets.token_hex(16)
            self._generation = secrets.token_hex(16)
            self._nonce = secrets.token_hex(16)
        except BaseException:
            self._socket.close()
            raise
        self._registry = None
        self._previous = None
        self._returned = None
        self._peer = None
        self._samples_received = 0
        self._velocity_samples_received = 0
        self._gaps_received = 0
        self._rejected_packets = 0
        self._last_sample = None
        self._last_nonzero_velocity_sample = None

    @property
    def socket(self):
        """Pass the bound loopback UDP socket to the platform peer validator."""
        return self._socket

    @property
    def local_generation(self):
        return self._generation

    @property
    def registry(self):
        """Verified (registry_epoch, npc_ship_index) bound by configure()."""
        return self._registry

    @property
    def peer(self):
        return self._peer

    def _owner(self):
        if threading.get_ident() != self._thread:
            raise RuntimeError('exclusive npc motion source owner thread required')

    def _prefix(self):
        return f'N|{self._lease}|{self._generation}|'.encode('ascii')

    def _report_once(self, stage):
        if self._on_stage is not None and stage not in self._reported_stages:
            self._reported_stages.add(stage)
            self._on_stage(stage)

    def configure(self, epoch, npc_index, *, operational_bits=None):
        """Bind this live sampler source once to one registered NPC ship index."""
        self._owner()
        if (self._registry is not None or self._socket.fileno() < 0
                or type(epoch) is not str or not _HEX32.fullmatch(epoch)
                or type(npc_index) is not int or not 1 <= npc_index <= 200000):
            raise ValueError('invalid registered npc motion source configuration')
        if len({epoch, self._generation, self._lease, self._nonce}) != 4:
            raise RuntimeError('npc motion source tokens overlap')
        if operational_bits is not None and (
            type(operational_bits) is not str or npc_index > len(operational_bits)
            or operational_bits[npc_index - 1] != '1'
        ):
            raise ValueError('registered npc target is unavailable in the verified register')
        self._registry = (epoch, npc_index)
        return {
            'port': self._socket.getsockname()[1],
            'lease': self._lease,
            'generation': self._generation,
            'nonce': self._nonce,
            'index': npc_index,
        }

    def stats(self):
        self._owner()
        return deepcopy({
            'samples_received': self._samples_received,
            'velocity_samples_received': self._velocity_samples_received,
            'gaps_received': self._gaps_received,
            'rejected_packets': self._rejected_packets,
            'last_sample': self._last_sample,
            'last_nonzero_velocity_sample': self._last_nonzero_velocity_sample,
        })

    def poll(self, validate_peer):
        """Return one latest validated V3 NPC sample, or None."""
        self._owner()
        if self._registry is None or not callable(validate_peer):
            raise ValueError('configured npc motion source and peer validator required')
        newest = None
        checked_peer = None
        prefix = self._prefix()
        for _ in range(32):
            try:
                raw, address = self._socket.recvfrom(_MAX_NPC_SAMPLER_DATAGRAM + 1)
            except BlockingIOError:
                break
            except OSError as error:
                if getattr(error, 'winerror', None) == 10040:
                    self._rejected_packets += 1
                    continue
                raise
            if not raw.startswith(prefix) or address[0] != '127.0.0.1':
                self._rejected_packets += 1
                continue
            self._report_once('first_local_npc_datagram_matched')
            if checked_peer != address:
                if self._peer is not None and address != self._peer:
                    self._rejected_packets += 1
                    continue
                if validate_peer(address) is not True:
                    self._rejected_packets += 1
                    continue
                checked_peer = self._peer = address
                self._report_once('first_local_npc_peer_verified')
            body = raw[len(prefix):]
            try:
                fields = body.decode('ascii').split('|')
            except UnicodeDecodeError:
                self._rejected_packets += 1
                continue
            if fields == ['GAP']:
                newest = None
                self._returned = None
                self._gaps_received += 1
                continue
            if len(raw) > _MAX_NPC_SAMPLER_DATAGRAM or len(fields) != 14 or fields[0] != 'SAMPLE3':
                self._rejected_packets += 1
                continue
            _, epoch, index_text, seq_text, sector, source_time, capture_time, x, y, z, vx, vy, vz, end = fields
            if (end != '' or not re.fullmatch(r'[1-9][0-9]{0,15}', seq_text)
                    or not re.fullmatch(r'[1-9][0-9]{0,5}', index_text)
                    or (epoch, int(index_text)) != self._registry):
                self._rejected_packets += 1
                continue
            try:
                rate = None if (vx, vy, vz) == ('-', '-', '-') else dict(zip('xyz', map(float, (vx, vy, vz))))
                sample = motion_value({
                    'version': 3,
                    'epoch': epoch,
                    'generation': self._generation,
                    'index': int(index_text),
                    'sequence': int(seq_text),
                    'sector_macro': sector,
                    'source_time': float(source_time),
                    'capture_time': float(capture_time),
                    'x': float(x),
                    'y': float(y),
                    'z': float(z),
                    'real_velocity': rate,
                })
            except ValueError:
                self._rejected_packets += 1
                continue
            if self._previous is not None:
                if sample['sequence'] <= self._previous['sequence']:
                    continue
                if sample['sector_macro'] != self._previous['sector_macro']:
                    sample['real_velocity'] = None
                try:
                    check_motion_progress(sample, self._previous)
                except ValueError:
                    self._rejected_packets += 1
                    continue
            self._previous = newest = sample
            self._samples_received += 1
            self._last_sample = sample
            if sample['real_velocity'] is not None:
                self._velocity_samples_received += 1
                if any(v != 0 for v in sample['real_velocity'].values()):
                    self._last_nonzero_velocity_sample = sample
            self._report_once('first_local_npc_sample_validated')
        if newest is not None:
            if select.select([self._socket], [], [], 0)[0]:
                return None  # Drain backlog before emitting an older sample.
            if self._returned != newest['sector_macro']:
                newest = dict(newest, real_velocity=None)
            self._returned = newest['sector_macro']
        return newest

    def close(self):
        self._owner()
        self._socket.close()
