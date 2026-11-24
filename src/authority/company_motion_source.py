"""Own the loopback receive side of the selected X4 player's read-only sampler."""
import re
import secrets
import select
import socket
import threading

from .experimental_tcp_motion import check_motion_progress, motion_value


class CompanyMotionSource:
    """Poll the latest authenticated sample on the same thread as the game owner."""

    def __init__(self):
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
        self._previous = self._returned = None
        self._peer = None

    @property
    def socket(self):
        """Pass the bound socket to the platform's owned-X4 peer validator."""
        return self._socket

    @property
    def local_generation(self):
        return self._generation

    @property
    def registry(self):
        """Verified (registry epoch, selected index), populated by the installer."""
        return self._registry

    def _owner(self):
        if threading.get_ident() != self._thread:
            raise RuntimeError('exclusive company motion owner thread required')

    def configure(self, epoch, index):
        """Bind this source once to the installed overlay's verified selection."""
        self._owner()
        if (self._registry is not None or self._socket.fileno() < 0 or
                type(epoch) is not str or not re.fullmatch(r'[0-9a-f]{32}', epoch) or
                type(index) is not int or not 1 <= index <= 200000):
            raise ValueError('invalid selected company motion configuration')
        if len({epoch, self._generation, self._lease, self._nonce}) != 4:
            raise RuntimeError('company motion tokens overlap')
        self._registry = (epoch, index)
        return {'port': self._socket.getsockname()[1], 'lease': self._lease,
                'generation': self._generation, 'nonce': self._nonce, 'index': index}

    def poll(self, validate_peer):
        """Return one latest new V3 sample, or None. validate_peer takes (IPv4, port)."""
        self._owner()
        if self._registry is None or not callable(validate_peer):
            raise ValueError('configured company motion source and validator required')
        newest = None
        checked_peer = None
        prefix = f'C|{self._lease}|{self._generation}|'.encode('ascii')
        for _ in range(32):
            try:
                raw, address = self._socket.recvfrom(513)
            except BlockingIOError:
                break
            if not raw.startswith(prefix) or address[0] != '127.0.0.1':
                continue
            if checked_peer != address:
                if self._peer is not None and address != self._peer:
                    continue
                if validate_peer(address) is not True:
                    continue
                checked_peer = self._peer = address
            fields = raw[len(prefix):].decode('ascii').split('|')
            if fields == ['GAP']:
                newest = None
                self._returned = None
                continue
            if len(raw) > 512 or len(fields) != 14 or fields[0] != 'SAMPLE3':
                raise ValueError('invalid local company motion packet')
            _, epoch, index, sequence, sector, source_time, capture_time, x, y, z, vx, vy, vz, end = fields
            if (end != '' or not re.fullmatch(r'[1-9][0-9]{0,15}', sequence)
                    or not re.fullmatch(r'[1-9][0-9]{0,5}', index)
                    or (epoch, int(index)) != self._registry):
                raise ValueError('foreign local company motion sample')
            rate = None if (vx, vy, vz) == ('-', '-', '-') else dict(zip('xyz', map(float, (vx, vy, vz))))
            sample = motion_value({
                'version': 3, 'epoch': epoch, 'generation': self._generation,
                'index': int(index), 'sequence': int(sequence), 'sector_macro': sector,
                'source_time': float(source_time), 'capture_time': float(capture_time),
                'x': float(x), 'y': float(y), 'z': float(z), 'real_velocity': rate,
            })
            if self._previous is not None:
                if sample['sequence'] <= self._previous['sequence']:
                    continue
                if sample['sector_macro'] != self._previous['sector_macro']:
                    sample['real_velocity'] = None
                check_motion_progress(sample, self._previous)
            self._previous = newest = sample
        if newest is not None:
            if select.select([self._socket], [], [], 0)[0]:
                return None  # Drain a backlog before publishing an old observation.
            # A coalesced first sample or sector transition has no rate in the
            # receiver's frame until a preceding observation in that frame.
            if self._returned != newest['sector_macro']:
                newest = dict(newest, real_velocity=None)
            self._returned = newest['sector_macro']
        return newest

    def close(self):
        self._owner()
        self._socket.close()
