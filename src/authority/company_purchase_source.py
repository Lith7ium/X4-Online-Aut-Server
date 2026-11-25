"""Cockpit trades on the existing owned-client loopback boundary."""
import re
import secrets
import socket
import threading
import time

from .company_purchase_statements import CompanyPurchaseStatements


_MAX_FRAME = 512
_MAX_INTEGER = 2147483647
_MAX_EXACT = 9007199254740991
_CODE = re.compile(r'[A-Z0-9]{3}-[A-Z0-9]{3}')
_SECTOR = re.compile(r'[A-Za-z0-9_]{1,128}')
_WARE = re.compile(r'[a-z][a-z0-9_]{0,127}')
_SALE_ID = re.compile(r'[0-9a-f]{32}')
_SALE_NUMBER = re.compile(r'[1-9][0-9]{0,9}')
_SALE_MAX_UNITS = 65535
_SALE_VIEW_TTL = 90
_MARKET_KEYS = ('view_id', 'sector', 'station_code', 'offset')
_MARKET_PAGE_SIZE = 8
_MARKET_MAX_OFFERS = 4096
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

    def __init__(self, *, expected_epoch=None, server_fingerprint=None, state_dir=None,
                 undock=False, trade_view=False, sales=False):
        if type(undock) is not bool:
            raise ValueError('invalid Company undock test flag')
        if type(trade_view) is not bool:
            raise ValueError('invalid Company trade view flag')
        if type(sales) is not bool or (sales and not trade_view):
            raise ValueError('Company sales require an enabled Company trade view')
        self.undock, self.trade_view, self.sales = undock, trade_view, sales
        self._sale_view = self._sale_intent = self._sale_answer = self._sale_ship = None
        self._market_serial = 0
        self._market_request = self._market_packets = None
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
        self._notice = None
        self._notice_serial = 0
        self._next_notice_scan = 0.

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
        if self.trade_view:
            config['trade_view'] = True
        if self.sales:
            config['sales'] = True
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

    def _receive_notice(self, fields, peer):
        if self._statements is None:
            return
        if fields == ['NOTICE_POLL']:
            if self._notice is None:
                now = time.monotonic()
                if now < self._next_notice_scan:
                    return
                if self._validator(peer) is not True:
                    return
                self._notice = self._statements.next_notice()
                # New local results invalidate this delay immediately. Check
                # occasionally for receipts written by an earlier client process.
                self._next_notice_scan = now + 30
                if self._notice is None:
                    return
                self._notice_serial += 1
            elif self._validator(peer) is not True:
                return
            self._peer = peer
            notice = self._notice
            message = (f"NOTICE|{self._notice_serial}|{notice['purchase_id']}|{notice['ware']}|"
                       f"{notice['quantity']}|{notice['station_code']}|{notice['settled_total_cents']}")
            try:
                self._socket.sendto(self._prefix + message.encode('ascii'), peer)
            except BlockingIOError:
                pass  # A later poll retries the same serial.
        elif (self._notice is not None and self._peer == peer
              and fields == ['NOTICE_ACK', str(self._notice_serial), self._notice['purchase_id']]
              and self._validator(peer) is True):
            self._statements.acknowledge_notice(self._notice['purchase_id'])
            self._notice = None
            self._next_notice_scan = 0.

    def _send_market(self):
        if self._market_packets is None or self._validator(self._peer) is not True:
            return
        for packet in self._market_packets:
            try:
                self._socket.sendto(packet, self._peer)
            except BlockingIOError:
                break  # The native receiver repeats requests until each whole page arrives.

    def _receive_market(self, row, peer):
        if not self.trade_view or len(row) not in (2, 5):
            return
        kind, view_id, *data = row
        if not view_id.startswith(self._lease[:24]) or not re.fullmatch(r'[0-9a-f]{32}', view_id):
            return
        serial = int(view_id[24:], 16)
        if serial == 0 or serial < self._market_serial or self._validator(peer) is not True:
            return
        if kind == 'MARKET_CLOSE' and not data:
            self._market_serial, self._market_request, self._market_packets = serial, None, None
            return
        if kind != 'MARKET' or len(data) != 3:
            return
        sector, station, raw_offset = data
        if (not _SECTOR.fullmatch(sector) or not _CODE.fullmatch(station)
                or not re.fullmatch(r'0|[1-9][0-9]{0,3}', raw_offset)):
            return
        offset = int(raw_offset)
        if offset >= _MARKET_MAX_OFFERS or offset % _MARKET_PAGE_SIZE:
            return
        request = dict(zip(_MARKET_KEYS, (view_id, sector, station, offset)))
        if serial > self._market_serial:
            if offset:
                return
        elif request == self._market_request:
            self._send_market()
            return
        elif (self._market_request is None or self._market_packets is None
              or any(request[k] != self._market_request[k] for k in _MARKET_KEYS[:-1])
              or offset != self._market_request['offset'] + _MARKET_PAGE_SIZE):
            return
        if self.sales and serial > self._market_serial:
            self._sale_view = dict(view_id=view_id, sector=sector, station_code=station, offers={},
                                   sale_id=None, used=False, expires=time.monotonic() + _SALE_VIEW_TTL)
        self._peer = peer
        self._market_serial, self._market_request, self._market_packets = serial, request, None

    def market_exchange(self, page_or_none):
        """Bridge a single admitted view page to the owned local trade window."""
        self._owner()
        if not self.trade_view or self._validator is None:
            raise RuntimeError('Company trade view IPC is not enabled and bound')
        self._receive()
        request = self._market_request
        if request is None or self._validator(self._peer) is not True:
            return None
        if (self._market_packets is None and type(page_or_none) is dict
                and all(type(page_or_none.get(key)) is type(value) and page_or_none[key] == value
                        for key, value in request.items())):
            from .company_trade_view import view_page

            page = view_page(page_or_none)
            prefix = [request['view_id'], str(request['offset'])]
            outcome = page['outcome']
            if outcome == 'observed':
                offers = page['offers']
                header = ['MARKET_PAGE', *prefix, outcome, str(page['company_balance_cents']),
                          str(page['total_offers']), str(len(offers))]
                rows = [['MARKET_ROW', *prefix, str(index), *(str(offer[key]) for key in
                         ('side', 'ware', 'price_cents', 'amount', 'min_amount', 'cargo_units', 'free_units')),
                         '1' if offer['tradeable'] else '0']
                        for index, offer in enumerate(offers, 1)]
                if self.sales:
                    sale_id = self._record_sale_page(page)
                    if sale_id is not None:
                        header.append(sale_id)
                contents = [header, *rows]
            else:
                contents = [['MARKET_PAGE', *prefix, outcome, page['reason']]]
            self._market_packets = tuple(self._prefix + '|'.join(row).encode('ascii') for row in contents)
            self._send_market()
        # Keep the authoritative observation alive while this window owns it.
        # A later offset advances the same view; CLOSE or a new ID retires it.
        return dict(request)

    def _record_sale_page(self, page):
        view = self._sale_view
        if view is None or time.monotonic() >= view['expires']:
            return None
        for offer in page['offers']:
            if offer['side'] == 'sell' and offer['tradeable']:
                key = offer['ware'], offer['price_cents']
                # Ambiguous duplicate bids are not independently selectable.
                view['offers'][key] = (None if key in view['offers'] else
                                       (max(1, offer['min_amount']),
                                        min(_SALE_MAX_UNITS, offer['amount'], offer['cargo_units'])))
        if page['offset'] + len(page['offers']) == page['total_offers']:
            view['sale_id'] = secrets.token_hex(16)
            return view['sale_id']
        return None

    def _send_sale_answer(self):
        if self._sale_answer is not None and self._validator(self._peer) is True:
            try:
                self._socket.sendto(self._sale_answer, self._peer)
            except BlockingIOError:
                pass  # The same local request ID retrieves the retained result.

    def _receive_sale(self, row, peer):
        if not self.sales or len(row) != 8:
            return
        sale_id, view_id, sector, station, ware, quantity, price, ship = row
        if (not _SALE_ID.fullmatch(sale_id) or not _SALE_ID.fullmatch(view_id)
                or not _SECTOR.fullmatch(sector) or not _CODE.fullmatch(station)
                or not _WARE.fullmatch(ware) or not _CODE.fullmatch(ship)
                or not _SALE_NUMBER.fullmatch(quantity) or not _SALE_NUMBER.fullmatch(price)
                or self._validator(peer) is not True):
            return
        quantity, price = int(quantity), int(price)
        intent = dict(sale_id=sale_id, sector=sector, station_code=station, ware=ware,
                      quantity=quantity, price_cents=price)
        if self._sale_intent is not None:
            if self._sale_intent['sale_id'] == sale_id:
                if intent == self._sale_intent and ship == self._sale_ship:
                    self._send_sale_answer()
                return
            if self._sale_answer is None:
                return
        view = self._sale_view
        if (view is None or view['used'] or time.monotonic() >= view['expires']
                or int(view_id[24:], 16) != self._market_serial
                or (view_id, sector, station, sale_id) !=
                   (view['view_id'], view['sector'], view['station_code'], view['sale_id'])
                or price > _MAX_INTEGER or (self._intent is not None and self._answer is None)):
            return
        limits = view['offers'].get((ware, price))
        if limits is None or not limits[0] <= quantity <= limits[1]:
            return
        view['used'] = True
        self._peer, self._sale_intent, self._sale_ship, self._sale_answer = peer, intent, ship, None

    def sale_exchange(self, receipt_or_none):
        """Return the active sale value to the bound poll or deliver its terminal receipt."""
        self._owner()
        if not self.sales or self._validator is None:
            raise RuntimeError('Company sale IPC is not enabled and bound')
        self._receive()
        intent = self._sale_intent
        if (intent is None or self._sale_answer is not None or self._validator(self._peer) is not True):
            return None
        receipt = receipt_or_none
        if (type(receipt) is not dict or any(type(receipt.get(key)) is not type(value) or receipt[key] != value
                                             for key, value in intent.items())):
            return dict(intent)
        outcome = receipt.get('outcome')
        ship = receipt.get('ship_code')
        if ship is not None and ship != self._sale_ship:
            return dict(intent)
        if outcome == 'confirmed' and ship == self._sale_ship:
            try:
                fields = [_amount(receipt.get('credited_cents'), 1, _MAX_EXACT),
                          _amount(receipt.get('company_balance_cents'), 0, _MAX_EXACT),
                          _amount(receipt.get('cargo_units'), 0, _MAX_INTEGER)]
            except ValueError:
                return dict(intent)
        elif outcome == 'refused' and receipt.get('reason') == 'price_changed':
            price = receipt.get('current_price_cents')
            if type(price) is not int or not 1 <= price <= _MAX_INTEGER or price == intent['price_cents']:
                return dict(intent)
            fields = ['price_changed', str(price)]
        elif outcome in ('refused', 'unknown'):
            fields = []
        else:
            return dict(intent)
        self._sale_answer = self._prefix + '|'.join(
            ('SALE_RECEIPT', intent['sale_id'], self._sale_ship, outcome, *fields)).encode('ascii')
        self._send_sale_answer()
        return None

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
            if fields[0] in ('NOTICE_POLL', 'NOTICE_ACK'):
                self._receive_notice(fields, peer)
                continue
            if fields[0] in ('MARKET', 'MARKET_CLOSE'):
                self._receive_market(fields, peer)
                continue
            if fields[0] == 'SALE':
                self._receive_sale(fields[1:], peer)
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
                    or (self._sale_intent is not None and self._sale_answer is None)
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
        trade_view_version = getattr(owner, '_company_trade_view_version', None)
        if self.trade_view and type(trade_view_version) is int and trade_view_version == 1:
            callbacks['company_trade_view_exchange'] = self.market_exchange
        if self.sales and getattr(owner, '_company_sales', False) is True:
            callbacks['company_sale_exchange'] = self.sale_exchange
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
        if self._statements is None or type(receipt) is not dict:
            return False
        current_basket = self._intent is not None and self._intent['purchase_id'] == receipt.get('purchase_id')
        if not self._statements.retain_result(receipt, queue_notice=not current_basket):
            return False
        if not current_basket:
            self._next_notice_scan = 0.
        elif self._awaiting_statement or self._answer is None:
            total = str(receipt['settled_total_cents'])
            self._answer = self._prefix + f'RECEIPT|{self._sequence}|pilot_reported|{total}'.encode('ascii')
            self._awaiting_statement = False
            self._send_receipt()
        return True

    def close(self):
        self._owner()
        self._socket.close()
