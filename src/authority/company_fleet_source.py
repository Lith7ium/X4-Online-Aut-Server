"""Pass one stock Fleet order at a time across the owned X4 loopback boundary."""

import re
import secrets
import socket
import threading

from .company_fleet_orders import fleet_order, fleet_receipt


_MAX_FRAME = 512
_DECIMAL = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:e[+-][0-9]+)?')


class CompanyFleetSource:
    """Correlate bounded local datagrams and authenticated control receipts."""

    def __init__(self, *, version=1):
        if type(version) is not int or version not in (1, 2):
            raise ValueError('unsupported Company fleet IPC version')
        self._version = version
        self._thread = threading.get_ident()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            self._socket.bind(('127.0.0.1', 0))
            self._socket.setblocking(False)
        except BaseException:
            self._socket.close()
            raise
        self._lease = secrets.token_hex(16)
        self._prefix = f'F|{self._lease}|'.encode('ascii')
        self._epoch = self._validator = self._peer = None
        self._packet = self._order = self._answer = None
        self._serial = 0
        self._offered = False

    @property
    def socket(self):
        return self._socket

    def _owner(self):
        if self._thread != threading.get_ident():
            raise RuntimeError('Company fleet IPC requires the owner thread')

    def configure(self, epoch):
        self._owner()
        if (self._epoch is not None or self._socket.fileno() < 0 or type(epoch) is not str
                or not re.fullmatch(r'[0-9a-f]{32}', epoch)):
            raise ValueError('invalid Company fleet IPC epoch')
        self._epoch = epoch
        settings = {'epoch': epoch, 'port': self._socket.getsockname()[1], 'lease': self._lease}
        if self._version == 2:
            settings['version'] = 2
        return settings

    def bind_validator(self, validate_peer):
        self._owner()
        if self._epoch is None or self._validator is not None or not callable(validate_peer):
            raise ValueError('configured Company fleet IPC and peer validator required')
        self._validator = validate_peer

    def _send_answer(self):
        if self._answer is not None and self._validator(self._peer) is True:
            try:
                self._socket.sendto(self._answer, self._peer)
            except BlockingIOError:
                pass  # The game retries the exact same order frame while it awaits a result.

    def _retain_receipt(self, result):
        if not self._offered or self._answer is not None:
            return
        try:
            receipt = fleet_receipt(result, version=self._version)
        except ValueError:
            return
        if any(receipt[key] != value for key, value in self._order.items()):
            return
        fields = ['RESULT', receipt['order_id'], receipt['outcome']]
        if receipt['outcome'] == 'refused':
            fields.append(receipt['reason'])
        self._answer = self._prefix + '|'.join(fields).encode('ascii')
        self._send_answer()

    def _decode_order(self, packet):
        fields = packet[len(self._prefix):].decode('ascii').split('|')
        expected_fields = 9 if self._version == 2 else 8
        if len(fields) != expected_fields or fields[0] != 'ORDER':
            raise ValueError('invalid Company fleet IPC frame')
        _, order_id, ship_index, ship_code, sector, x, y, z = fields[:8]
        if (not order_id.startswith(self._lease[:24]) or len(order_id) != 32
                or not re.fullmatch(r'[1-9][0-9]{0,5}', ship_index)
                or any(len(value) > 32 or not _DECIMAL.fullmatch(value) for value in (x, y, z))):
            raise ValueError('invalid Company fleet IPC fields')
        order = dict(order_id=order_id, ship_index=int(ship_index), ship_code=ship_code,
                     sector=sector, x=float(x), y=float(y), z=float(z))
        if self._version == 2:
            order['queue_mode'] = fields[8]
        return int(order_id[24:], 16), fleet_order(order, version=self._version)

    def _receive(self):
        for _ in range(16):
            try:
                packet, peer = self._socket.recvfrom(_MAX_FRAME + 1)
            except BlockingIOError:
                break
            except OSError as error:
                if getattr(error, 'winerror', None) == 10040:
                    continue
                raise
            if (len(packet) > _MAX_FRAME or not packet.startswith(self._prefix)
                    or peer[0] != '127.0.0.1' or self._peer not in (None, peer)):
                continue
            if packet == self._packet:
                self._send_answer()
                continue
            if self._order is not None and self._answer is None:
                continue
            try:
                serial, order = self._decode_order(packet)
            except ValueError:
                continue
            if serial <= self._serial or self._validator(peer) is not True:
                continue
            self._serial, self._peer, self._packet = serial, peer, packet
            self._order, self._answer, self._offered = order, None, False

    def exchange(self, receipt_or_none):
        """Deliver each verified order once to the current control connection."""
        self._owner()
        if self._validator is None:
            raise RuntimeError('Company fleet IPC is not bound to an owned X4 process')
        if receipt_or_none is not None:
            self._retain_receipt(receipt_or_none)
        self._receive()
        if self._order is not None and not self._offered and self._validator(self._peer) is True:
            self._offered = True
            return dict(self._order)
        return None

    def close(self):
        self._owner()
        self._socket.close()
