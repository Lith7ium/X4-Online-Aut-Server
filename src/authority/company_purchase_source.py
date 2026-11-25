"""One pending cockpit purchase on the existing owned-client loopback boundary."""
import re
import secrets
import socket
import threading

from .company_purchase_statements import CompanyPurchaseStatements


_MAX_FRAME = 512
_MAX_INTEGER = 2147483647
_MAX_EXACT = 9007199254740991
_CODE = re.compile(r'[A-Z0-9]{3}-[A-Z0-9]{3}')
_SECTOR = re.compile(r'[A-Za-z0-9_]{1,128}')
_WARE = re.compile(r'[a-z][a-z0-9_]{0,127}')
_UNDOCK_KEYS = ('request_id', 'sector', 'station_code', 'bay_seed', 'bay_macro', 'dockarea_macro')


def _valid_undock_berth(sector, station, seed, bay, area):
    if (not _SECTOR.fullmatch(sector) or not _CODE.fullmatch(station)
            or not re.fullmatch(r'-?[1-9][0-9]{0,19}', seed)
            or not _WARE.fullmatch(bay) or not _WARE.fullmatch(area)):
        return False
    return -(2**63) <= int(seed) <= 2**64 - 1


def _amount(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('invalid Company purchase amount')
    return str(value)


class CompanyPurchaseSource:
    """Exchange bounded IPC messages on the owner thread without waiting for X4."""

    def __init__(self, *, expected_epoch=None, server_fingerprint=None, state_dir=None, undock=False):
        if type(undock) is not bool:
            raise ValueError('invalid Company undock test flag')
        self.undock = undock
        self._undock_nonce = secrets.token_hex(16) if undock else None
        self._undock_request = secrets.token_hex(16) if undock else None
        self._undock_intent = self._undock_answer = None
        self._expected_epoch = expected_epoch
        self._server_fingerprint, self._state_dir = server_fingerprint, state_dir
        self._statements = self._displayed_total = None
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
        self._prefix = f'T|{self._lease}|'.encode('ascii')
        self._registry = self._validator = self._peer = self._intent = self._answer = None
        self._sequence = 0
        self._awaiting_statement = False

    @property
    def socket(self):
        return self._socket

    def _owner(self):
        if self._thread != threading.get_ident():
            raise RuntimeError('Company purchase source requires the owner thread')

    def configure(self, epoch, index):
        self._owner()
        if (self._registry is not None or self._socket.fileno() < 0 or type(epoch) is not str
                or (self._expected_epoch is not None and epoch != self._expected_epoch)
                or not re.fullmatch(r'[0-9a-f]{32}', epoch) or type(index) is not int or not 1 <= index <= 200000):
            raise ValueError('invalid Company purchase seat context')
        if self._server_fingerprint is not None:
            self._statements = CompanyPurchaseStatements(self._server_fingerprint, epoch, index,
                                                          state_dir=self._state_dir)
        self._registry = (epoch, index)
        config = {'epoch': epoch, 'index': index, 'port': self._socket.getsockname()[1], 'lease': self._lease}
        if self.undock:
            config.update(undock_nonce=self._undock_nonce, undock_request=self._undock_request)
        return config

    def bind_validator(self, validate_peer):
        self._owner()
        if self._registry is None or self._validator is not None or not callable(validate_peer):
            raise ValueError('configured Company purchase seat and peer validator required')
        self._validator = validate_peer

    def _retain_receipt(self, receipt):
        if (self._intent is None or self._answer is not None or type(receipt) is not dict
                or any(type(receipt.get(key)) is not type(value) or receipt.get(key) != value
                       for key, value in self._intent.items())):
            return  # An old or mismatched receipt does not settle the current request.
        outcome = receipt.get('outcome')
        fields = [outcome]
        if outcome == 'confirmed':
            ship = receipt.get('ship_code')
            if type(ship) is not str or not _CODE.fullmatch(ship):
                raise ValueError('invalid settled Company ship code')
            fields.extend((_amount(receipt.get('settled_total_cents'), 1, _MAX_EXACT),
                           _amount(receipt.get('company_balance_cents'), 0, _MAX_EXACT),
                           _amount(receipt.get('cargo_units'), self._intent['quantity'], _MAX_EXACT), ship))
        elif outcome == 'refused':
            reason = receipt.get('reason')
            if reason == 'price_changed':
                price = receipt.get('current_price_cents')
                if price == self._intent['price_cents']:
                    raise ValueError('stale Company quote did not change')
                fields.extend((reason, _amount(price, 1, _MAX_EXACT)))
            elif reason in ('captain_required', 'dock_mismatch', 'provider_busy'):
                fields.append(reason)
        elif outcome == 'accepted_pending_statement':
            self._awaiting_statement = True
        elif outcome != 'unknown':
            raise ValueError('invalid Company purchase outcome')
        answer = self._prefix + '|'.join(('RECEIPT', str(self._sequence), *fields)).encode('ascii')
        self._answer = answer

    def _send_receipt(self):
        if self._answer is not None and self._peer is not None and self._validator(self._peer) is True:
            try:
                self._socket.sendto(self._answer, self._peer)
            except BlockingIOError:
                pass  # The native owner repeats the same pending local request.

    def _send_undock(self):
        if self._undock_answer is not None and self._peer is not None and self._validator(self._peer) is True:
            try:
                self._socket.sendto(self._undock_answer, self._peer)
            except BlockingIOError:
                pass

    def _receive_undock(self, fields, peer):
        if (not self.undock or len(fields) != len(_UNDOCK_KEYS)
                or fields[0] != self._undock_request or not _valid_undock_berth(*fields[1:])
                or self._validator(peer) is not True):
            return
        value = dict(zip(_UNDOCK_KEYS, fields))
        if self._undock_intent is None:
            self._undock_intent, self._peer = value, peer
        if self._undock_intent == value:
            self._send_undock()

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
            try:
                fields = packet[len(self._prefix):].decode('ascii').split('|')
            except UnicodeError:
                continue
            if fields[0] == 'UNDOCK':
                self._receive_undock(fields[1:], peer)
                continue
            if len(fields) != 7:
                continue
            sequence, sector, station, ware, quantity, price, total = fields
            if (not _SECTOR.fullmatch(sector) or not _CODE.fullmatch(station) or not _WARE.fullmatch(ware)
                    or not all(re.fullmatch(r'[1-9][0-9]{0,9}', value) for value in (sequence, quantity, price))):
                continue
            if total != '-' and not re.fullmatch(r'[1-9][0-9]{0,15}', total):
                continue
            displayed_total = None if total == '-' else int(total)
            if displayed_total is not None and displayed_total > _MAX_EXACT:
                continue
            sequence, quantity, price = map(int, (sequence, quantity, price))
            if max(sequence, quantity, price) > _MAX_INTEGER or self._validator(peer) is not True:
                continue
            intent = {'sector': sector, 'station_code': station, 'ware': ware,
                      'quantity': quantity, 'price_cents': price}
            if sequence == self._sequence:
                if displayed_total == self._displayed_total and all(
                        self._intent[key] == value for key, value in intent.items()):
                    self._send_receipt()
                continue
            if (sequence != self._sequence + 1 or self._awaiting_statement
                    or (self._intent is not None and self._answer is None)):
                continue
            purchase = {'purchase_id': secrets.token_hex(16), **intent}
            if self._statements is not None and displayed_total is not None:
                self._statements.retain(purchase, displayed_total)
            self._peer, self._sequence, self._answer = peer, sequence, None
            self._displayed_total = displayed_total
            self._intent = purchase

    def exchange(self, receipt_or_none):
        """Consume an authoritative receipt and return the same ID until it settles."""
        self._owner()
        if self._validator is None:
            raise RuntimeError('Company purchase IPC is not bound to an owned X4 process')
        if receipt_or_none is not None:
            self._retain_receipt(receipt_or_none)
            self._send_receipt()
        self._receive()
        if self._intent is not None and self._answer is None and self._validator(self._peer) is True:
            return dict(self._intent)
        return None

    def undock_exchange(self, receipt_or_none):
        self._owner()
        if not self.undock or self._validator is None:
            raise RuntimeError('Company undock IPC is not enabled and bound')
        self._receive()
        if (self._undock_intent is not None and self._undock_answer is None
                and type(receipt_or_none) is dict and all(
                    receipt_or_none.get(key) == value for key, value in self._undock_intent.items())):
            outcome = receipt_or_none.get('outcome')
            if outcome not in ('confirmed', 'refused', 'unknown'):
                raise ValueError('invalid Company undock outcome')
            echo = [self._undock_intent[key] for key in _UNDOCK_KEYS]
            self._undock_answer = self._prefix + '|'.join(('UNDOCK_RESULT', outcome, *echo)).encode('ascii')
            self._send_undock()
        if (self._undock_intent is not None and self._undock_answer is None
                and self._validator(self._peer) is True):
            return dict(self._undock_intent)
        return None

    def control_callbacks(self, owner):
        self._owner()
        callbacks = {'company_purchase_exchange': self.exchange}
        if self.undock:
            callbacks['company_undock_exchange'] = self.undock_exchange
        version = getattr(owner, '_company_purchase_statement_version', None)
        if self._statements is not None and type(version) is int and version == 1:
            callbacks.update(company_purchase_statement=self.statement,
                             company_purchase_statement_result=self.statement_result)
        return callbacks

    def statement(self, purchase_id):
        self._owner()
        return None if self._statements is None else self._statements.statement(purchase_id)

    def statement_result(self, receipt):
        self._owner()
        if self._statements is None or type(receipt) is not dict or not self._statements.retain_result(receipt):
            return False
        if self._awaiting_statement and self._intent['purchase_id'] == receipt['purchase_id']:
            total = str(receipt['settled_total_cents'])
            self._answer = self._prefix + f'RECEIPT|{self._sequence}|pilot_reported|{total}'.encode('ascii')
            self._awaiting_statement = False
            self._send_receipt()
        return True

    def close(self):
        self._owner()
        self._socket.close()
