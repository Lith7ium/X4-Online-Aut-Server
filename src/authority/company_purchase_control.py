"""Opt-in purchase polling on an admitted, exclusively owned participant socket."""

import json
import re
import time

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
    elif outcome != 'unknown':
        raise ValueError('unknown company purchase outcome')
    if set(value) != keys:
        raise ValueError('unexpected company purchase receipt fields')
    return dict(value)


def _envelope(epoch, handle, attempt, index, sequence):
    if (not _matches(epoch, _EPOCH) or not _matches(attempt, _TOKEN) or not _int(handle)
            or not _int(index) or not _int(sequence)):
        raise ValueError('invalid company purchase participant binding')
    return {'version': 1, 'session_epoch': epoch, 'connection_handle': handle,
            'attempt_id': attempt, 'ship_index': index, 'request_id': sequence}


def _bound(value, envelope, kind, payload):
    return (type(value) is dict and set(value) == set(envelope) | {'kind', payload}
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

    def receipt(self, value):
        if value is None and self.pending is None:
            return None
        result = purchase_receipt(value)
        if self.pending is None or any(result[key] != self.pending[key] for key in _PURCHASE):
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

    def __init__(self, epoch, handle, attempt, ship_index, exchange):
        _envelope(epoch, handle, attempt, ship_index, 1)
        if not callable(exchange):
            raise ValueError('company purchase exchange must be callable')
        self.binding = (epoch, handle, attempt, ship_index)
        self.exchange = exchange
        self.window = _PurchaseWindow()

    def reply(self, command, check):
        envelope = _envelope(*self.binding, self.window.sequence + 1)
        if not _bound(command, envelope, _POLL, 'receipt'):
            raise ValueError('unexpected or stale company purchase poll')
        receipt = self.window.receipt(command['receipt'])
        check()
        purchase = self.window.next(self.exchange(receipt))
        check()
        reply = _frame(dict(envelope, kind=_RESULT, purchase=purchase))
        self.window.advance(purchase)
        return reply


def poll_company_purchase(participants, handle, attempt_id, ship_index, receipt=None, *, guard=None):
    """Poll one admitted participant; send one exact terminal receipt if pending.

    The caller explicitly enables this path after capability negotiation. The
    optional side-effect-free guard takes no arguments and must return True.
    Local preflight errors leave the connection idle. Transport failure retires
    only this peer. Loss of host or world ownership faults the adapter and host.
    """
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
                and not peer['in'] and not peer['out'] and control._session is session
                and session._peer(conn) == owner and session._admitted_ships.get(owner) is reservation)

    if not binding_current():
        raise ValueError('company purchase participant binding unavailable')
    window = peer.setdefault('company_purchase', _PurchaseWindow())
    envelope = _envelope(epoch, handle, attempt_id, reservation.ship_index, window.sequence + 1)
    result = window.receipt(receipt)
    if result is not None and result['outcome'] == 'confirmed' and result['ship_code'] != reservation.code:
        raise ValueError('company purchase receipt names a different ship')
    raw = _frame(dict(envelope, kind=_POLL, receipt=result))

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
        if not _bound(answer, envelope, _RESULT, 'purchase'):
            return False
        window.next(answer['purchase'])
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
            purchase = window.next(answer['purchase'])
            window.advance(purchase)
            return None if purchase is None else dict(purchase)
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
