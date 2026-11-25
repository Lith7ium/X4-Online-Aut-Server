"""Opt-in purchase polling on an admitted, exclusively owned participant socket."""

from collections import OrderedDict
import json
import re
import time

from .company_command_window import CompanyCommandWindow
from .company_fleet_orders import fleet_order, fleet_receipt
from .company_trade_view import view_page, view_request
from .company_undock_messages import UndockWindow
from .experimental_tcp_participants import ExperimentalTCPParticipants, ParticipantGuardFailure
from .load_messages import MAX_INTEGER, MAX_MESSAGE


_PURCHASE = {'purchase_id', 'sector', 'station_code', 'ware', 'quantity', 'price_cents'}
_TOKEN = re.compile(r'[0-9a-f]{32}')
_EPOCH = re.compile(r'[A-Za-z0-9_-]{1,64}')
_CODE = re.compile(r'[A-Z0-9]{3}-[A-Z0-9]{3}')
_SECTOR = re.compile(r'[A-Za-z0-9_]{1,128}')
_WARE = re.compile(r'[a-z][a-z0-9_]{0,127}')
_REASON = re.compile(r'[a-z][a-z0-9_]{0,63}')
_POLL = 'company_purchase_poll'
_RESULT = 'company_purchase_result'
_STATEMENT = 'company_purchase_statement'
_STATEMENT_RESULT = 'company_purchase_statement_result'
_STATEMENT_DELIVERY = 'company_purchase_statement_delivery'
_STATEMENT_ACK = 'company_purchase_statement_ack'
_STATEMENT_VERSION = 1
_MAX_PURCHASES = 4096


class CompanyPurchasePeerLost(ConnectionError):
    """Only the selected participant was retired during purchase polling."""

    def __init__(self, handle):
        self.handle = handle
        super().__init__('company purchase participant connection lost')


def _int(value, *, minimum=1, maximum=MAX_INTEGER):
    return type(value) is int and minimum <= value <= maximum


def _matches(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def purchase_value(value):
    if (type(value) is not dict or set(value) != _PURCHASE
            or not _matches(value['purchase_id'], _TOKEN)
            or not _matches(value['sector'], _SECTOR)
            or not _matches(value['station_code'], _CODE)
            or not _matches(value['ware'], _WARE)
            or not _int(value['quantity'], maximum=2147483647)
            or not _int(value['price_cents'])):
        raise ValueError('invalid company purchase')
    return dict(value)


def purchase_receipt(value):
    if type(value) is not dict or not _PURCHASE <= value.keys():
        raise ValueError('invalid company purchase receipt')
    purchase_value({name: value[name] for name in _PURCHASE})
    outcome = value.get('outcome')
    keys = _PURCHASE | {'outcome'}
    if outcome == 'confirmed':
        keys |= {'settled_total_cents', 'company_balance_cents', 'cargo_units', 'ship_code'}
        if (not _int(value.get('settled_total_cents'))
                or not _int(value.get('company_balance_cents'), minimum=0)
                or not _int(value.get('cargo_units'), minimum=0)
                or not _matches(value.get('ship_code'), _CODE)):
            raise ValueError('invalid confirmed company purchase receipt')
    elif outcome == 'refused':
        keys.add('reason')
        if not _matches(value.get('reason'), _REASON):
            raise ValueError('invalid company purchase refusal reason')
        if value['reason'] == 'price_changed':
            keys.add('current_price_cents')
            if not _int(value.get('current_price_cents')) or value['current_price_cents'] == value['price_cents']:
                raise ValueError('invalid changed company purchase quote')
    elif outcome == 'pilot_reported':
        keys.add('settled_total_cents')
        if not _int(value.get('settled_total_cents')):
            raise ValueError('invalid pilot-reported total')
    elif outcome not in ('unknown', 'accepted_pending_statement'):
        raise ValueError('unknown company purchase outcome')
    if set(value) != keys:
        raise ValueError('unexpected company purchase receipt fields')
    return dict(value)


def _statement_value(value, purchase_id):
    if value is None:
        return None
    if (type(value) is not dict
            or set(value) != {'purchase_id', 'ware', 'quantity', 'settled_total_cents'}
            or value['purchase_id'] != purchase_id or not _matches(value['ware'], _WARE)
            or not _int(value['quantity'], maximum=2147483647)
            or not _int(value['settled_total_cents'])):
        raise ValueError('invalid company purchase statement')
    return dict(value)


def _statement_envelope(envelope, purchase_id, challenge):
    if not _matches(purchase_id, _TOKEN) or not _matches(challenge, _TOKEN):
        raise ValueError('invalid company purchase statement challenge')
    return dict(envelope, statement_version=_STATEMENT_VERSION,
                purchase_id=purchase_id, challenge=challenge)


def _reported_receipt(value):
    result = purchase_receipt(value)
    if result['outcome'] != 'pilot_reported':
        raise ValueError('pilot-reported purchase receipt required')
    return result


def _statement_version_current(peer):
    version = peer.get('company_purchase_statement_version')
    return type(version) is int and version == _STATEMENT_VERSION


def _envelope(epoch, handle, attempt, index, sequence):
    if (not _matches(epoch, _EPOCH) or not _matches(attempt, _TOKEN) or not _int(handle)
            or not _int(index) or not _int(sequence)):
        raise ValueError('invalid company purchase participant binding')
    return {'version': 1, 'session_epoch': epoch, 'connection_handle': handle,
            'attempt_id': attempt, 'ship_index': index, 'request_id': sequence}


def _bound(value, envelope, kind, payload):
    fields = {'kind'} if payload is None else {'kind', payload}
    return (type(value) is dict and set(value) == set(envelope) | fields
            and value['kind'] == kind
            and all(type(value[key]) is type(expected) and value[key] == expected
                    for key, expected in envelope.items()))


def _frame(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')
    if len(raw) > MAX_MESSAGE:
        raise ValueError('company purchase frame too large')
    return raw + b'\n'


class _PurchaseWindow:
    def __init__(self):
        self.sequence = 0
        self.pending = None
        self.seen = set()
        self.challenges = OrderedDict()
        self.undock = UndockWindow()
        self.fleet = CompanyCommandWindow(fleet_order, fleet_receipt, 'order_id')

    def check_challenge(self, challenge):
        if challenge in self.challenges:
            raise ValueError('reused company purchase statement challenge')

    def advance_challenge(self, challenge):
        # The sequence already binds every frame. The durable ledger also
        # enforces uniqueness per purchase across sessions and older challenges.
        self.challenges[challenge] = None
        if len(self.challenges) > _MAX_PURCHASES:
            self.challenges.popitem(last=False)
        self.sequence += 1

    def receipt(self, value):
        if value is None and self.pending is None:
            return None
        result = purchase_receipt(value)
        if (result['outcome'] == 'pilot_reported' or self.pending is None
                or any(result[key] != self.pending[key] for key in _PURCHASE)):
            raise ValueError('company purchase receipt does not match the outstanding request')
        return result

    def next(self, value):
        if value is None:
            return None
        result = purchase_value(value)
        if result['purchase_id'] in self.seen or len(self.seen) >= _MAX_PURCHASES:
            raise ValueError('reused or exhausted company purchase ID')
        return result

    def advance(self, result):
        self.sequence += 1
        self.pending = result
        if result is not None:
            self.seen.add(result['purchase_id'])


class ClientPurchaseControl:
    """Consume one host poll on the client's owned control thread."""

    def __init__(self, epoch, handle, attempt, ship_index, exchange, statement=None, statement_result=None, *,
                 undock_exchange=None, trade_view_exchange=None, fleet_exchange=None):
        _envelope(epoch, handle, attempt, ship_index, 1)
        callbacks = (exchange, statement, statement_result, undock_exchange, trade_view_exchange, fleet_exchange)
        if all(value is None for value in callbacks) or any(
                value is not None and not callable(value) for value in callbacks):
            raise ValueError('invalid company purchase callbacks')
        self.binding = (epoch, handle, attempt, ship_index)
        self.exchange = exchange
        self.undock_exchange = undock_exchange
        self.fleet_exchange = fleet_exchange
        self.trade_view_exchange = trade_view_exchange
        self.statement = statement
        self.statement_result = statement_result
        self.window = _PurchaseWindow()

    def reply(self, command, check):
        envelope = _envelope(*self.binding, self.window.sequence + 1)
        kind = command.get('kind') if type(command) is dict else None
        if kind == 'company_undock_poll':
            return self._reply_order(command, envelope, check, self.window.undock, self.undock_exchange,
                                     'company_undock_result', 'departure')
        if kind == 'company_fleet_poll':
            return self._reply_order(command, envelope, check, self.window.fleet, self.fleet_exchange,
                                     'company_fleet_result', 'order')
        if type(command) is dict and command.get('kind') == _STATEMENT:
            return self._reply_statement(command, envelope, check)
        if type(command) is dict and command.get('kind') == _STATEMENT_DELIVERY:
            return self._reply_statement_delivery(command, envelope, check)
        market_poll = type(command) is dict and command.get('kind') == 'company_trade_poll'
        if market_poll:
            if self.trade_view_exchange is None or not _bound(command, envelope, 'company_trade_poll', 'trade'):
                raise ValueError('unexpected or stale Company trade poll')
            trade = command['trade']
            if type(trade) is not dict or set(trade) != {'receipt', 'page'}:
                raise ValueError('invalid Company trade poll')
            page = None if trade['page'] is None else view_page(trade['page'])
            receipt = self.window.receipt(trade['receipt'])
        else:
            if not _bound(command, envelope, _POLL, 'receipt'):
                raise ValueError('unexpected or stale company purchase poll')
            receipt = self.window.receipt(command['receipt'])
        if self.exchange is None:
            raise ValueError('Company purchase exchange is unavailable')
        check()
        purchase = self.window.next(self.exchange(receipt))
        check()
        if market_poll:
            request = self.trade_view_exchange(page)
            if request is not None:
                request = view_request(request)
            check()
            reply = _frame(dict(envelope, kind='company_trade_result',
                                trade={'purchase': purchase, 'view': request}))
        else:
            reply = _frame(dict(envelope, kind=_RESULT, purchase=purchase))
        self.window.advance(purchase)
        return reply


    def _reply_order(self, command, envelope, check, window, exchange, response_kind, payload):
        if exchange is None or not _bound(command, envelope, command['kind'], 'receipt'):
            raise ValueError('unexpected or stale Company command poll')
        receipt = window.receipt(command['receipt'])
        check()
        request = window.next_request(exchange(receipt))
        check()
        reply = _frame(dict(envelope, kind=response_kind, **{payload: request}))
        window.advance(request)
        self.window.sequence += 1
        return reply

    def _reply_statement(self, command, envelope, check):
        purchase_id, challenge = command.get('purchase_id'), command.get('challenge')
        envelope = _statement_envelope(envelope, purchase_id, challenge)
        if self.statement is None or not _bound(command, envelope, _STATEMENT, None):
            raise ValueError('unexpected or stale company purchase statement request')
        self.window.check_challenge(challenge)
        check()
        statement = _statement_value(self.statement(purchase_id), purchase_id)
        check()
        reply = _frame(dict(envelope, kind=_STATEMENT_RESULT, statement=statement))
        self.window.advance_challenge(challenge)
        return reply

    def _reply_statement_delivery(self, command, envelope, check):
        envelope = dict(envelope, statement_version=_STATEMENT_VERSION)
        if self.statement_result is None or not _bound(command, envelope, _STATEMENT_DELIVERY, 'receipt'):
            raise ValueError('unexpected or stale company purchase statement delivery')
        receipt = _reported_receipt(command['receipt'])
        check()
        if self.statement_result(dict(receipt)) is not True:
            raise ValueError('pilot-reported purchase was not acknowledged by client')
        check()
        reply = _frame(dict(envelope, kind=_STATEMENT_ACK, purchase_id=receipt['purchase_id']))
        self.window.sequence += 1
        return reply


def supports_trade_view(peer):
    version = peer.get('trade_view_version')
    return type(version) is int and version == 1


def poll_company_purchase(participants, handle, attempt_id, ship_index, receipt=None, *,
                          guard=None, market=False, page=None):
    """Poll one admitted participant; send one exact terminal receipt if pending.

    The caller explicitly enables this path after capability negotiation. The
    optional side-effect-free guard takes no arguments and must return True.
    Local preflight errors leave the connection idle. Transport failure retires
    only this peer. Loss of host or world ownership faults the adapter and host.
    """
    def prepare(peer, window, reservation, envelope):
        result = window.receipt(receipt)
        if (result is not None and result['outcome'] == 'accepted_pending_statement'
                and not _statement_version_current(peer)):
            raise ValueError('pending statement requires negotiated version 1')
        if result is not None and result['outcome'] == 'confirmed' and result['ship_code'] != reservation.code:
            raise ValueError('company purchase receipt names a different ship')
        if market:
            checked_page = None if page is None else view_page(page)
            command = dict(envelope, kind='company_trade_poll', trade={'receipt': result, 'page': checked_page})

            def parse_trade(value):
                if type(value) is not dict or set(value) != {'purchase', 'view'}:
                    raise ValueError('invalid Company trade poll response')
                request = None if value['view'] is None else view_request(value['view'])
                return {'purchase': window.next(value['purchase']), 'view': request}

            def advance_trade(value):
                window.advance(value['purchase'])

            return _frame(command), envelope, 'company_trade_result', 'trade', parse_trade, advance_trade
        if page is not None:
            raise ValueError('market page requires a negotiated Company trade view')
        return (_frame(dict(envelope, kind=_POLL, receipt=result)), envelope,
                _RESULT, 'purchase', window.next, window.advance)

    return _exchange_purchase(participants, handle, attempt_id, ship_index, prepare, guard,
                              peer_supports=supports_trade_view if market else None)


def poll_company_undock(participants, handle, attempt_id, ship_index, receipt=None, *, guard=None):
    """Exchange one departure on the same admitted, negotiated control socket."""
    return _poll_company_order(participants, handle, attempt_id, ship_index, receipt, guard,
                               'undock', 'departure', 'company_undock_version')


def poll_company_fleet(participants, handle, attempt_id, ship_index, receipt=None, *, guard=None):
    """Exchange one MoveWait request and an outstanding receipt with the admitted pilot."""
    return _poll_company_order(participants, handle, attempt_id, ship_index, receipt, guard,
                               'fleet', 'order', 'fleet_order_version')


def _poll_company_order(participants, handle, attempt_id, ship_index, receipt, guard,
                        service, payload, version_field):
    def prepare(peer, window, reservation, envelope):
        commands = getattr(window, service)
        checked = commands.receipt(receipt)

        def advance(request):
            commands.advance(request)
            window.sequence += 1

        command = dict(envelope, kind=f'company_{service}_poll', receipt=checked)
        return (_frame(command), envelope, f'company_{service}_result', payload,
                commands.next_request, advance)

    def negotiated(peer):
        version = peer.get(version_field)
        return type(version) is int and version == 1

    return _exchange_purchase(participants, handle, attempt_id, ship_index, prepare, guard,
                              peer_supports=negotiated)


def request_company_purchase_statement(participants, handle, attempt_id, ship_index,
                                       purchase_id, challenge, *, guard=None):
    """Ask an admitted pilot for one statement, with a fresh caller-issued challenge.

    A response is the pilot's statement. The caller binds it to persisted purchase
    provenance and compares independent responses before adopting any claim.
    """
    def prepare(peer, window, reservation, envelope):
        envelope = _statement_envelope(envelope, purchase_id, challenge)
        window.check_challenge(challenge)
        return (_frame(dict(envelope, kind=_STATEMENT)), envelope,
                _STATEMENT_RESULT, 'statement', lambda value: _statement_value(value, purchase_id),
                lambda _value: window.advance_challenge(challenge))

    return _exchange_purchase(participants, handle, attempt_id, ship_index, prepare, guard,
                              peer_supports=_statement_version_current)


def deliver_company_purchase_statement_result(participants, handle, attempt_id, ship_index,
                                              receipt, *, guard=None):
    """Deliver an adopted pilot statement and wait for a correlated acknowledgement."""
    receipt = _reported_receipt(receipt)

    def parse(purchase_id):
        if purchase_id != receipt['purchase_id']:
            raise ValueError('pilot-reported purchase acknowledgement differs')

    def prepare(peer, window, reservation, envelope):
        envelope = dict(envelope, statement_version=_STATEMENT_VERSION)

        def advance(_value):
            window.sequence += 1

        return (_frame(dict(envelope, kind=_STATEMENT_DELIVERY, receipt=receipt)), envelope,
                _STATEMENT_ACK, 'purchase_id', parse, advance)

    return _exchange_purchase(participants, handle, attempt_id, ship_index, prepare, guard,
                              peer_supports=_statement_version_current)


def _exchange_purchase(participants, handle, attempt_id, ship_index, prepare, guard, *,
                       peer_supports=None):
    if not isinstance(participants, ExperimentalTCPParticipants) or (guard is not None and not callable(guard)):
        raise ValueError('owned company purchase participants and optional guard required')
    host = participants._host
    host._check()
    control = host._control
    control._reader_free()
    if participants._busy or participants._failed:
        raise RuntimeError('company purchase participant adapter unavailable')

    world = host._world.session
    epoch = world.world_epoch
    _envelope(epoch, handle, attempt_id, ship_index, 1)
    matches = [(conn, peer) for conn, peer in control._peers.items() if peer['handle'] == handle]
    if len(matches) != 1:
        raise ValueError('company purchase participant unavailable')
    conn, peer = matches[0]
    session = control._session
    session._live()
    owner = session._peer(conn)
    reservation = session._admitted_ships.get(owner)
    registry_epoch = peer.get('company_purchase_epoch')
    if (reservation is None or reservation.ship_index != ship_index
            or not _matches(registry_epoch, _TOKEN)):
        raise ValueError('company purchase differs from admitted ship')

    def binding_current():
        return (not participants._failed and not host._failed and not control._failed and not control._closed
                and not session._failed and not session._inner._failed
                and host._world.session is world and world.world_epoch == epoch
                and world.state == 'running' and world.pending is None
                and any(h == handle for h, _ in world.ready_players)
                and control._peers.get(conn) is peer and peer['handle'] == handle
                and peer['stage'] == 'admitted' and peer['attempt_id'] == attempt_id
                and type(peer.get('company_purchase_ship')) is int and peer['company_purchase_ship'] == ship_index
                and peer.get('company_purchase_epoch') == registry_epoch
                and (peer_supports is None or peer_supports(peer))
                and not peer['in'] and not peer['out'] and control._session is session
                and session._peer(conn) == owner and session._admitted_ships.get(owner) is reservation)

    if not binding_current():
        raise ValueError('company purchase participant binding unavailable')
    window = peer.setdefault('company_purchase', _PurchaseWindow())
    envelope = _envelope(epoch, handle, attempt_id, reservation.ship_index, window.sequence + 1)
    raw, envelope, kind, payload, parse, advance = prepare(peer, window, reservation, envelope)

    lease = object()
    control._reader_lease = lease
    host._busy = participants._busy = True
    deadline = time.monotonic() + min(participants._timeout, 2)
    started = False

    def check():
        try:
            allowed = guard is None or guard() is True
        except Exception as error:
            raise ParticipantGuardFailure('company purchase world guard failed') from error
        if (not allowed or control._reader_lease is not lease or not host._busy or not participants._busy
                or peer.get('company_purchase') is not window or not binding_current()):
            raise ParticipantGuardFailure('company purchase participant binding changed')

    def validate(answer):
        if not _bound(answer, envelope, kind, payload):
            return False
        parse(answer[payload])
        return True

    loss = None
    try:
        check()
        peers = {handle: (conn, peer)}
        started = True
        try:
            participants._quiet(peers, deadline)
            answer = participants._exchange(
                conn, peer, [], raw, None, deadline, check=check, validate_answer=validate)
            participants._quiet(peers, deadline)
        except ParticipantGuardFailure:
            raise
        except (OSError, RuntimeError, ValueError) as error:
            check()
            loss = error
        if loss is None:
            check()
            result = parse(answer[payload])
            advance(result)
            return result
    except BaseException:
        if started:
            participants._failed = host._failed = True
        raise
    finally:
        participants._busy = host._busy = False
        if control._reader_lease is lease:
            control._reader_lease = None
        else:
            participants._failed = host._failed = True
            raise RuntimeError('company purchase reader ownership changed')

    try:
        host._check()
        control._reader_free()
        if control._peers.get(conn) is not peer:
            raise RuntimeError('company purchase peer changed before retirement')
        control._drop(conn, transport_lost=True)
    except BaseException:
        participants._failed = host._failed = True
        raise
    raise CompanyPurchasePeerLost(handle) from loss
