"""Read-only, paged station offers for an admitted pilot's stock trade window."""

from dataclasses import dataclass, field
import re

from .load_messages import MAX_INTEGER

# Eight maximum-sized rows leave room for a purchase receipt in a 4 KiB control frame.
PAGE_SIZE = 8
MAX_OFFERS = 4096
_MAX_UNITS = (1 << 31) - 1
_REQUEST_FIELDS = {'view_id', 'sector', 'station_code', 'offset'}
_OFFER_FIELDS = {'side', 'ware', 'price_cents', 'amount', 'min_amount', 'cargo_units', 'free_units', 'tradeable'}


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def _text(value, pattern):
    return type(value) is str and re.fullmatch(pattern, value) is not None


def view_request(value):
    if (type(value) is not dict or set(value) != _REQUEST_FIELDS
            or not _text(value['view_id'], '[0-9a-f]{32}')
            or not _text(value['sector'], '[A-Za-z0-9_]{1,128}')
            or not _text(value['station_code'], '[A-Z0-9]{3}-[A-Z0-9]{3}')
            or not _integer(value['offset'], 0, MAX_OFFERS)
            or value['offset'] % PAGE_SIZE):
        raise ValueError('invalid Company market view request')
    return dict(value)


def _offer(value):
    if (type(value) is not dict or set(value) != _OFFER_FIELDS
            or value['side'] not in ('buy', 'sell') or type(value['tradeable']) is not bool
            or not _text(value['ware'], '[a-z][a-z0-9_]{0,127}')
            or not _integer(value['price_cents'], 1, MAX_INTEGER)
            or not all(_integer(value[key], 0, _MAX_UNITS)
                       for key in ('amount', 'min_amount', 'cargo_units', 'free_units'))):
        raise ValueError('invalid Company market offer')
    return dict(value)


def _market_result(value, *, page):
    if type(value) is not dict:
        raise ValueError('invalid Company market result')
    outcome = value.get('outcome')
    if outcome in ('refused', 'unavailable'):
        if set(value) != {'outcome', 'reason'} or not _text(value['reason'], '[a-z][a-z0-9_]{0,63}'):
            raise ValueError('invalid Company market refusal')
        return dict(value)
    fields = {'outcome', 'company_balance_cents', 'offers'}
    if page:
        fields.add('total_offers')
    if (outcome != 'observed' or set(value) != fields
            or not _integer(value['company_balance_cents'], 0, MAX_INTEGER)
            or type(value['offers']) is not list
            or len(value['offers']) > (PAGE_SIZE if page else MAX_OFFERS)):
        raise ValueError('invalid Company market snapshot')
    return dict(value, offers=[_offer(offer) for offer in value['offers']])


def view_page(value):
    if type(value) is not dict or not _REQUEST_FIELDS <= value.keys():
        raise ValueError('missing Company market page identity')
    request = view_request({key: value[key] for key in _REQUEST_FIELDS})
    result = _market_result({key: item for key, item in value.items() if key not in _REQUEST_FIELDS}, page=True)
    if result['outcome'] == 'observed':
        total, offset = result['total_offers'], request['offset']
        if (not _integer(total, 0, MAX_OFFERS) or (offset and offset >= total)
                or len(result['offers']) != min(PAGE_SIZE, total - offset)):
            raise ValueError('inconsistent Company market page')
    return {**request, **result}


@dataclass
class _OpenView:
    request: dict
    snapshot: dict | None = None
    sent_offsets: set[int] = field(default_factory=set)


class CompanyTradeViews:
    """Keep one immutable market observation per currently interested pilot."""

    def __init__(self, native):
        self._native = native
        self._views = {}
        self._reading = None

    def request(self, binding, request):
        if request is None:
            self._views.pop(binding, None)
            return
        request = view_request(request)
        previous = self._views.get(binding)
        if previous is not None and previous.request['view_id'] == request['view_id']:
            if all(previous.request[key] == request[key] for key in ('sector', 'station_code')):
                previous.request = request
                return
            # A view ID belongs to one station for the entire admission.
            previous.request = request
            previous.snapshot = {'outcome': 'refused', 'reason': 'view_binding_changed'}
            return
        self._views.pop(binding, None)
        state = _OpenView(request)
        if request['offset'] != 0:
            state.snapshot = {'outcome': 'unavailable', 'reason': 'reopen_trade_window'}
        self._views[binding] = state

    def page(self, binding):
        state = self._views.get(binding)
        if state is None or state.snapshot is None:
            return None
        snapshot = state.snapshot
        if snapshot['outcome'] != 'observed':
            return {**state.request, **snapshot}
        offers = snapshot['offers']
        offset = state.request['offset']
        if offset and offset >= len(offers):
            return {**state.request, 'outcome': 'unavailable', 'reason': 'reopen_trade_window'}
        return dict(state.request, outcome='observed', company_balance_cents=snapshot['company_balance_cents'],
                    total_offers=len(offers), offers=offers[offset:offset + PAGE_SIZE])

    def has_unsent_page(self, binding):
        state = self._views.get(binding)
        return (state is not None and state.snapshot is not None
                and state.request['offset'] not in state.sent_offsets)

    def mark_page_sent(self, binding):
        state = self._views[binding]
        state.sent_offsets.add(state.request['offset'])

    def step(self, bindings, *, can_start):
        """Advance one read without executing an economic order or delaying a world transition."""
        self._views = {binding: view for binding, view in self._views.items()
                       if bindings.get(binding.handle) == binding}
        if self._reading is not None:
            binding, view = self._reading
            if self._views.get(binding) is not view or view.snapshot is not None:
                self._native.cancel()
                self._reading = None
            else:
                self._finish(view, self._native.poll())
                return
        if can_start:
            for binding, view in self._views.items():
                if view.snapshot is None:
                    identity = dict(binding.native_value(), sector=view.request['sector'],
                                    station_code=view.request['station_code'])
                    self._reading = (binding, view)
                    self._finish(view, self._native.begin(identity))
                    return

    def _finish(self, view, result):
        if result is not None:
            view.snapshot = _market_result(result, page=False)
            self._reading = None

    def reset(self):
        if self._reading is not None:
            self._native.cancel()
            self._reading = None
        self._views.clear()
