"""Exchange confirmed pilot impacts through an established loopback TLS bridge."""
import errno
import select
import socket
import time

from .company_combat_frames import (BINDING_FIELDS, decode_receipts, derive_combat_ticket,
                                    encode_frame, read_frame, validate_impacts)
from .trusted_load_messages import validate_ship_motion_credential


_CONNECT_TIMEOUT = 2
_CANCEL_POLL = .05
_MAX_EXCHANGE_TIMEOUT = 600
_CONNECTING = {getattr(errno, name) for name in (
    'EINPROGRESS', 'EWOULDBLOCK', 'EALREADY', 'EINTR',
) if hasattr(errno, name)} | {10035, 10036, 10037}  # WinSock progress codes.


class CombatExchangeCancelled(Exception):
    """The owner stopped waiting for the outstanding combat receipt."""


class CompanyCombatClient:
    """Send one batch at a time from the admitted pilot's combat worker.

    `exchange([{id, target_index, amount}, ...])` blocks for validated receipts.
    Number confirmed primary-gun impacts from 1 in capture order for this
    admission and submit contiguous batches. On I/O failure retry the same
    batch with this object. A retry may return earlier, versioned receipts.
    `order` is global to the provider lifetime and is retained across retries.
    Share an absolute monotonic `deadline` across retry calls, or pass a per-call
    `timeout` in seconds.
    A supplied `cancelled` callback can stop connection, write, or receipt waits.
    """

    def __init__(self, port, credential):
        if type(port) is not int or not 1 <= port <= 65535 or type(credential) is not dict:
            raise ValueError('invalid Company combat bridge or admission')
        credential = validate_ship_motion_credential(credential, index=credential.get('index'),
                                                     session_epoch=credential.get('session_epoch'))
        self._port = port
        self._binding = credential['generation'], credential['epoch'], credential['index']
        self._ticket = derive_combat_ticket(credential['ticket'])
        self._connection = None
        self._incoming = bytearray()
        self._pending = None
        self._pending_frame = None

    def exchange(self, impacts, *, timeout=None, deadline=None, cancelled=None):
        now = time.monotonic()
        if deadline is None:
            timeout = 5 if timeout is None else timeout
            if type(timeout) not in (float, int) or not 0 < timeout <= _MAX_EXCHANGE_TIMEOUT:
                raise ValueError('invalid Company combat exchange timeout')
            deadline = now + timeout
        elif (timeout is not None or type(deadline) not in (float, int)
              or not now - _MAX_EXCHANGE_TIMEOUT <= deadline <= now + _MAX_EXCHANGE_TIMEOUT):
            raise ValueError('invalid Company combat exchange deadline')
        if cancelled is not None and not callable(cancelled):
            raise ValueError('invalid Company combat cancellation callback')
        impacts = [dict(impact) for impact in validate_impacts(impacts, self._binding[2])]
        if self._pending is None:
            self._pending_frame = encode_frame(dict(zip(BINDING_FIELDS, self._binding), kind='combat_impacts',
                                                    ticket=self._ticket, impacts=impacts))
            self._pending = impacts
        elif self._pending != impacts:
            raise ValueError('retry the pending Company combat batch unchanged')
        try:
            self._remaining(deadline, cancelled)
            if self._connection is None:
                self._connect(min(deadline, time.monotonic() + _CONNECT_TIMEOUT), cancelled)
            outgoing = memoryview(self._pending_frame)
            while outgoing:
                self._wait_socket(deadline, cancelled, write=True)
                try:
                    sent = self._connection.send(outgoing)
                except BlockingIOError:
                    continue
                if not sent:
                    raise ConnectionError('Company combat stream closed while sending')
                outgoing = outgoing[sent:]
            while True:
                self._wait_socket(deadline, cancelled, write=False)
                receipt = read_frame(self._connection, self._incoming)
                if receipt is not None:
                    if self._incoming:
                        raise ValueError('unexpected trailing data in Company combat receipt')
                    receipts = decode_receipts(receipt, self._binding, impacts)
                    self._pending = None
                    self._pending_frame = None
                    return receipts
        except BaseException:
            self.close()
            raise

    def _connect(self, deadline, cancelled):
        conn = self._connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conn.setblocking(False)
        status = conn.connect_ex(('127.0.0.1', self._port))
        if status in _CONNECTING:
            self._wait_socket(deadline, cancelled, write=True)
            status = conn.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        if status:
            raise OSError(status, 'Company combat bridge connection failed')
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def _wait_socket(self, deadline, cancelled, *, write):
        conn = self._connection
        while True:
            remaining = self._remaining(deadline, cancelled)
            wait = remaining if cancelled is None else min(remaining, _CANCEL_POLL)
            readable, writable, exceptional = select.select(
                [] if write else [conn], [conn] if write else [], [conn], wait)
            if exceptional:
                raise ConnectionError('Company combat socket failed')
            if readable or writable:
                self._remaining(deadline, cancelled)
                return

    @staticmethod
    def _remaining(deadline, cancelled):
        if cancelled is not None and cancelled():
            raise CombatExchangeCancelled
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Company combat receipt deadline expired')
        return remaining

    def close(self):
        """Release the socket, preserving any uncertain batch for an exact retry."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        self._incoming.clear()
