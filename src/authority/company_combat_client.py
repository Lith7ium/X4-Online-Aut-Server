"""Exchange confirmed pilot impacts through an established loopback TLS bridge."""
import math
import socket
import time

from .company_combat_frames import (BINDING_FIELDS, decode_receipts, derive_combat_ticket,
                                    encode_frame, read_frame, validate_impacts)
from .trusted_load_messages import validate_ship_motion_credential


class CompanyCombatClient:
    """Send one batch at a time from the admitted pilot's combat worker.

    `exchange([{id, target_index, amount}, ...])` blocks for validated receipts.
    Number confirmed primary-gun impacts from 1 in capture order for this
    admission and submit contiguous batches. On I/O failure retry the same
    batch with this object. A retry may return earlier, versioned receipts.
    `order` is global to the provider lifetime and is retained across retries.
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

    def exchange(self, impacts, *, timeout=5):
        if type(timeout) not in (float, int) or not 0 < timeout <= 120 or not math.isfinite(timeout):
            raise ValueError('invalid Company combat exchange timeout')
        impacts = [dict(impact) for impact in validate_impacts(impacts, self._binding[2])]
        if self._pending is not None and self._pending != impacts:
            raise ValueError('retry the pending Company combat batch unchanged')
        frame = encode_frame(dict(zip(BINDING_FIELDS, self._binding), kind='combat_impacts',
                                  ticket=self._ticket, impacts=impacts))
        self._pending = impacts
        deadline = time.monotonic() + timeout
        try:
            if self._connection is None:
                self._connection = socket.create_connection(('127.0.0.1', self._port), timeout=timeout)
                self._connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._connection.settimeout(self._remaining(deadline))
            self._connection.sendall(frame)
            while True:
                self._connection.settimeout(self._remaining(deadline))
                receipt = read_frame(self._connection, self._incoming)
                if receipt is not None:
                    if self._incoming:
                        raise ValueError('unexpected trailing data in Company combat receipt')
                    receipts = decode_receipts(receipt, self._binding, impacts)
                    self._pending = None
                    return receipts
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _remaining(deadline):
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
