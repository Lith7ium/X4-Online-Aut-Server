"""Read the docked ship's live market from the supervisor's owned X4 process."""
from dataclasses import dataclass, field
import math
import re
import secrets
import socket
import time

from authority_native_purchase import _COMPONENT, _valid_binding
from authority_tcp_job_client_live import socket_owner


_FIELDS = ('registry_epoch', 'ship_index', 'ship_code', 'sector', 'station_code')
_ACTOR = {'pid', 'start_ticks', 'exe_dev', 'exe_ino'}
_SECTOR = re.compile(r'[A-Za-z0-9_]{1,128}')
_WARE = re.compile(r'[a-z][a-z0-9_]{0,127}')
_DECIMAL = re.compile(r'0|[1-9][0-9]{0,15}')
_REFUSED = {'invalid_registry', 'dock_mismatch'}
_UNAVAILABLE = {'account_unavailable', 'invalid_observation', 'market_limit'}
_MAX_PACKET = 768
_MAX_ROWS = 512
_MAX_PAGE = 16
_MAX_INTEGER = 2**31 - 1
_MAX_EXACT = 2**53 - 1
_RESEND_SECONDS = .25


def _amount(text, low, high):
    if not _DECIMAL.fullmatch(text) or not low <= int(text) <= high:
        raise ValueError('invalid market integer')
    return int(text)


def _offer(text):
    side, ware, price, amount, minimum, cargo, free = text.split(',')
    if side not in ('buy', 'sell') or not _WARE.fullmatch(ware):
        raise ValueError('invalid market offer identity')
    return {'side': side, 'ware': ware, 'price_cents': _amount(price, 1, _MAX_INTEGER),
            'amount': _amount(amount, 0, _MAX_INTEGER), 'min_amount': _amount(minimum, 0, _MAX_INTEGER),
            'cargo_units': _amount(cargo, 0, _MAX_INTEGER), 'free_units': _amount(free, 0, _MAX_INTEGER)}


@dataclass
class _Observation:
    nonce: str
    opening: bytes
    deadline: float
    due: float
    balance: int | None = None
    count: int | None = None
    offers: list = field(default_factory=list)


class NativeMarketSurveyProvider:
    """Nonblocking, one dock-bound observation at a time.

    The supervisor owns process lifetime and calls this on its game-control
    thread. Cancel on world/session changes before advertising any snapshot.
    """

    def __init__(self, *, timeout=35, clock=time.monotonic):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ValueError('invalid market observation timeout')
        self._timeout, self._clock = timeout, clock
        self._lease = secrets.token_hex(16)
        self._process = self._guard = self._peer = self._active = None
        self._serial = 0
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._socket.bind(('127.0.0.1', 0))
            self._socket.setblocking(False)
        except BaseException:
            self._socket.close()
            raise

    @property
    def lua_settings(self):
        """Lease and provider-local UDP port for the protected native overlay."""
        return self._lease, self._socket.getsockname()[1]

    def bind(self, process, guard):
        if self._process is not None or not callable(guard) or not _ACTOR <= process.keys():
            raise ValueError('market observation requires one owned X4 process')
        self._process, self._guard = dict(process), guard

    def begin(self, binding):
        """Start one read for the currently reserved registry index and station."""
        try:
            key = {name: binding[name] for name in _FIELDS}
        except (TypeError, KeyError):
            raise ValueError('incomplete native market binding') from None
        if (not _valid_binding(key)
                or type(key['sector']) is not str or not _SECTOR.fullmatch(key['sector'])
                or type(key['station_code']) is not str or not _COMPONENT.fullmatch(key['station_code'])):
            raise ValueError('invalid native market binding')
        if self._active is not None:
            return {'outcome': 'unavailable', 'reason': 'provider_busy'}
        if self._process is None:
            return {'outcome': 'unavailable', 'reason': 'provider_unavailable'}
        if self._serial == _MAX_INTEGER:
            return {'outcome': 'unavailable', 'reason': 'market_limit'}
        self._serial += 1
        nonce = secrets.token_hex(16)
        fields = (key['registry_epoch'], str(key['ship_index']), key['ship_code'],
                  key['sector'], key['station_code'])
        opening = '|'.join(('MARKET_OPEN', self._lease, nonce, str(self._serial), *fields)).encode('ascii')
        now = self._clock()
        self._active = _Observation(nonce, opening, now + self._timeout, now)
        return self.poll()

    def cancel(self):
        """Drop a pending survey. A later observation uses a new nonce/serial."""
        self._active = None

    def close(self):
        self.cancel()
        self._socket.close()

    def _owns(self, source):
        try:
            return (self._process is not None and self._guard() is not False
                    and socket_owner(source, self._socket.getsockname(), self._process))
        except (OSError, ValueError, RuntimeError):
            return False

    def _failure(self, reason):
        self.cancel()
        return {'outcome': 'refused' if reason in _REFUSED else 'unavailable', 'reason': reason}

    def _reply(self, kind, fields):
        active = self._active
        if kind == 'MARKET_HEADER' and len(fields) == 2:
            balance = _amount(fields[0], 0, _MAX_EXACT)
            count = _amount(fields[1], 0, _MAX_ROWS)
            if active.count is None:
                active.balance, active.count = balance, count
                active.due = self._clock()
            elif (balance, count) != (active.balance, active.count):
                raise ValueError('market header changed within the observation')
        elif kind == 'MARKET_PAGE' and active.count is not None and 2 <= len(fields) <= _MAX_PAGE + 1:
            offset = _amount(fields[0], 0, _MAX_ROWS)
            if offset < len(active.offers):
                return None  # The resender may still have an earlier page in flight.
            if offset != len(active.offers) or offset + len(fields) - 1 > active.count:
                raise ValueError('market page offset or size differs')
            active.offers.extend(_offer(row) for row in fields[1:])
            active.due = self._clock()
        elif kind == 'MARKET_FAIL' and len(fields) == 1 and fields[0] in _REFUSED | _UNAVAILABLE:
            return self._failure(fields[0])
        else:
            raise ValueError('unexpected market response')
        if active.count == len(active.offers):
            self.cancel()
            return {'outcome': 'observed', 'company_balance_cents': active.balance, 'offers': active.offers}
        return None

    def poll(self):
        active = self._active
        if active is not None and self._clock() >= active.deadline:
            return self._failure('timeout')
        hello = f'MARKET_HELLO|{self._lease}'.encode('ascii')
        for _ in range(32):
            try:
                packet, source = self._socket.recvfrom(_MAX_PACKET + 1)
            except BlockingIOError:
                break
            if self._peer is None and packet == hello and self._owns(source):
                self._peer = source
            if active is None or source != self._peer:
                continue
            parts = packet.split(b'|')
            if len(parts) < 3:
                continue
            kind, lease, nonce, *fields = parts
            if lease != self._lease.encode('ascii') or nonce != active.nonce.encode('ascii'):
                continue
            if len(packet) > _MAX_PACKET:
                return self._failure('invalid_observation')
            if not self._owns(source):
                return self._failure('provider_unavailable')
            if self._clock() >= active.deadline:
                return self._failure('timeout')
            try:
                result = self._reply(kind.decode('ascii'), [value.decode('ascii') for value in fields])
            except ValueError:
                return self._failure('invalid_observation')
            if result is not None:
                return result
        if active is None or self._peer is None or self._clock() < active.due:
            return None
        if not self._owns(self._peer):
            return self._failure('provider_unavailable')
        request = active.opening if active.count is None else (
            f'MARKET_READ|{self._lease}|{active.nonce}|{len(active.offers)}'.encode('ascii'))
        active.due = self._clock() + _RESEND_SECONDS
        try:
            self._socket.sendto(request, self._peer)
        except BlockingIOError:
            pass
        except OSError:
            return self._failure('provider_unavailable')
        return None
